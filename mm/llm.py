"""One LLMProvider interface; Bedrock, Anthropic API and a mock behind it. Every call is logged."""
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from mm import config


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMProvider(ABC):
    name = "base"
    model = "none"

    @abstractmethod
    def complete(self, *, purpose: str, system: str, prompt: str, temperature: float = 0.0) -> LLMResult: ...

    def cost(self, r: LLMResult) -> float:
        return (r.input_tokens * config.PRICE_IN + r.output_tokens * config.PRICE_OUT) / 1_000_000


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(self):
        import boto3
        self.model = os.environ.get("MM_BEDROCK_MODEL", "")
        if not self.model:
            raise SystemExit("Set MM_BEDROCK_MODEL to a Bedrock model or inference-profile id you have access to.")
        self.client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "eu-west-2"))

    def complete(self, *, purpose, system, prompt, temperature=0.0) -> LLMResult:
        t0 = time.time()
        r = self.client.converse(modelId=self.model, system=[{"text": system}],
                                 messages=[{"role": "user", "content": [{"text": prompt}]}],
                                 inferenceConfig={"temperature": temperature, "maxTokens": 8000})
        u = r["usage"]
        return LLMResult(r["output"]["message"]["content"][0]["text"], u["inputTokens"], u["outputTokens"],
                         int((time.time() - t0) * 1000))


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        import anthropic
        self.model = os.getenv("MM_ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY

    def complete(self, *, purpose, system, prompt, temperature=0.0) -> LLMResult:
        t0 = time.time()
        r = self.client.messages.create(model=self.model, max_tokens=8000, temperature=temperature, system=system,
                                        messages=[{"role": "user", "content": prompt}])
        return LLMResult(r.content[0].text, r.usage.input_tokens, r.usage.output_tokens, int((time.time() - t0) * 1000))


def make_provider(name: str | None = None, chaos: bool = False) -> LLMProvider:
    name = name or config.PROVIDER
    if name == "mock":
        from mm.mock_llm import MockProvider
        return MockProvider(chaos=chaos)
    if name == "bedrock":
        return BedrockProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise SystemExit(f"Unknown provider {name!r} (use mock, bedrock or anthropic)")


class Gateway:
    """Wraps a provider: logs prompt, response, tokens, cost and latency for every call."""

    def __init__(self, provider: LLMProvider, store, scan_id: int):
        self.provider, self.store, self.scan_id = provider, store, scan_id

    def call(self, purpose: str, system: str, prompt: str, subject: str, attempt: int = 1,
             temperature: float = 0.0) -> tuple[str, int]:
        r = self.provider.complete(purpose=purpose, system=system, prompt=prompt, temperature=temperature)
        call_id = self.store.log_call(scan_id=self.scan_id, purpose=purpose, subject=subject, attempt=attempt,
                                      provider=self.provider.name, model=self.provider.model, prompt=prompt,
                                      response=r.text, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                                      cost_usd=self.provider.cost(r), latency_ms=r.latency_ms)
        return r.text, call_id

    def mark(self, call_id: int, valid: bool, error: str = "") -> None:
        self.store.mark_call(call_id, valid, error)


def extract_json(text: str):
    """Pull a JSON object out of an LLM reply (tolerates code fences and stray prose)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found in the reply")
    return json.loads(s[start:end + 1])
