-- Home energy grant scheme (PostgreSQL 16). Re-runnable: drops existing tables first.
DROP TABLE IF EXISTS aud_log, pymt, msr, app, schm, epc_cert, hh_mbr, hh, prop, ppl, ref_cd, stf, org CASCADE;

CREATE TABLE org (
  org_id INT PRIMARY KEY,
  org_nm VARCHAR(120),
  org_typ CHAR(3),            -- LA = local authority, CON = contractor, ASB = assessor body
  prnt_org_id INT REFERENCES org(org_id)
);
CREATE TABLE stf (
  stf_id INT PRIMARY KEY,
  org_id INT,                 -- no FK declared
  fst_nm VARCHAR(60),
  lst_nm VARCHAR(60),
  role_cd VARCHAR(10),
  mgr_id INT                  -- no FK declared
);
CREATE TABLE ref_cd (
  cd_grp VARCHAR(20),
  cd VARCHAR(10),
  cd_desc VARCHAR(200),
  PRIMARY KEY (cd_grp, cd)
);
CREATE TABLE ppl (
  ppl_id INT PRIMARY KEY,
  fst_nm VARCHAR(60),
  lst_nm VARCHAR(60),
  dob DATE,
  nino CHAR(9),
  email_addr VARCHAR(200),
  ph_no VARCHAR(20),
  crt_ts TIMESTAMP
);
CREATE TABLE prop (
  prop_id INT PRIMARY KEY,
  uprn BIGINT,
  addr_l1 VARCHAR(120),
  addr_l2 VARCHAR(120),
  twn VARCHAR(60),
  pcode VARCHAR(8),
  la_org_id INT REFERENCES org(org_id),
  prop_typ_cd VARCHAR(10),
  yr_blt SMALLINT
);
CREATE TABLE hh (
  hh_id INT PRIMARY KEY,
  prop_id INT REFERENCES prop(prop_id),
  hh_sz SMALLINT,
  inc_band_cd VARCHAR(10),
  vuln_flg BOOLEAN
);
CREATE TABLE hh_mbr (
  hh_id INT REFERENCES hh(hh_id),
  ppl_id INT REFERENCES ppl(ppl_id),
  rel_cd VARCHAR(10),
  is_lead BOOLEAN,
  PRIMARY KEY (hh_id, ppl_id)
);
CREATE TABLE epc_cert (
  cert_id INT PRIMARY KEY,
  prop_id INT REFERENCES prop(prop_id),
  lodg_dt DATE,
  curr_band CHAR(1),
  pot_band CHAR(1),
  curr_scr SMALLINT,
  co2_tpy NUMERIC(6,2),
  assr_id INT                 -- no FK declared, points to stf
);
CREATE TABLE schm (
  schm_id INT PRIMARY KEY,
  schm_nm VARCHAR(120),
  fund_src VARCHAR(60),
  max_awd_amt NUMERIC(10,2),
  strt_dt DATE,
  end_dt DATE
);
CREATE TABLE app (
  app_id INT PRIMARY KEY,
  hh_id INT REFERENCES hh(hh_id),
  schm_id INT REFERENCES schm(schm_id),
  sts_cd VARCHAR(10),
  sub_dt DATE,
  dcsn_dt DATE,
  rsn_txt TEXT
);
CREATE TABLE msr (
  msr_id INT PRIMARY KEY,
  app_id INT REFERENCES app(app_id),
  msr_typ_cd VARCHAR(10),
  inst_dt DATE,
  cost_amt NUMERIC(10,2),
  contr_id INT                -- no FK declared, points to org
);
CREATE TABLE pymt (
  pymt_id INT PRIMARY KEY,
  app_id INT REFERENCES app(app_id),
  pymt_amt NUMERIC(10,2),
  pymt_dt DATE,
  pyee_typ VARCHAR(10),
  bnk_ref VARCHAR(30)
);
CREATE TABLE aud_log (
  log_id BIGINT PRIMARY KEY,
  tbl_nm VARCHAR(30),
  rec_id INT,
  act VARCHAR(10),
  act_by INT,
  act_ts TIMESTAMP,
  chg_json JSONB
);
