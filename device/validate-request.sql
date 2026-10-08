-- In a PRIVATE SQLite connection. Raw input is never executed as SQL.
CREATE TEMP TABLE req AS SELECT CAST(readfile(path) AS TEXT) body FROM input;
CREATE TEMP TABLE valid_json(v INTEGER CHECK(v=1));
INSERT INTO valid_json SELECT json_valid(body) FROM req;
CREATE TEMP TABLE invalid(reason TEXT);
INSERT INTO invalid SELECT 'duplicate JSON key' FROM req,json_tree(req.body) j WHERE j.key IS NOT NULL GROUP BY j.parent,j.key HAVING count(*)>1;
INSERT INTO invalid SELECT 'request fields' FROM req WHERE
 (SELECT count(*) FROM json_each(body))<>CASE json_extract(body,'$.schema') WHEN 'kc-edit-request/v2' THEN 12 ELSE 11 END OR EXISTS(SELECT 1 FROM json_each(body) WHERE key NOT IN
 ('schema','job_id','plan_id','plan_digest','device','library_uuid','snapshot_digest','policy_version','capabilities_version','operations','request_digest','new_books'));
INSERT INTO invalid SELECT 'schema' FROM req WHERE json_extract(body,'$.schema') NOT IN('kc-edit-request/v1','kc-edit-request/v2');
INSERT INTO invalid SELECT 'header types' FROM req WHERE
 json_type(body,'$.operations') IS NOT 'array' OR json_array_length(body,'$.operations') NOT BETWEEN 1 AND 20000
 OR json_type(body,'$.policy_version') IS NOT 'integer' OR json_extract(body,'$.policy_version')<0
 OR json_type(body,'$.device') IS NOT 'object' OR (SELECT count(*) FROM json_each(body,'$.device'))<>3;
INSERT INTO invalid SELECT 'device fields' FROM req,json_each(req.body,'$.device') d
 WHERE d.key NOT IN('instance_id','storage_uuid','serial_sha256') OR
 (d.key='instance_id' AND (d.type<>'text' OR length(d.value) NOT BETWEEN 1 AND 256)) OR
 (d.key<>'instance_id' AND d.type NOT IN('text','null'));
INSERT INTO invalid SELECT 'missing identifier' FROM req,json_each(req.body) j
 WHERE j.key IN('job_id','plan_id','library_uuid','capabilities_version') AND (j.type<>'text' OR length(j.value) NOT BETWEEN 1 AND 256);
INSERT INTO invalid SELECT 'invalid hash' FROM req,json_each(req.body) j WHERE j.key IN('request_digest','plan_digest','snapshot_digest')
 AND (j.type<>'text' OR length(j.value)<>64 OR j.value GLOB '*[^0-9a-f]*');
CREATE TEMP TABLE operations AS SELECT CAST(j.key AS INTEGER) ordinal,j.value body FROM req,json_each(req.body,'$.operations') j;
INSERT INTO invalid SELECT 'operation fields' FROM operations WHERE json_type(body)<>'object'
 OR (SELECT count(*) FROM json_each(body))<>8 OR EXISTS(SELECT 1 FROM json_each(body) WHERE key NOT IN
 ('op_id','kind','collection_uuid','before','after','args','depends_on','reason'));
INSERT INTO invalid SELECT 'operation identity/type' FROM operations WHERE json_type(body,'$.op_id') IS NOT 'text'
 OR length(json_extract(body,'$.op_id')) NOT BETWEEN 1 AND 256 OR json_type(body,'$.collection_uuid') IS NOT 'text'
 OR length(json_extract(body,'$.collection_uuid')) NOT BETWEEN 1 AND 256
 OR json_type(body,'$.reason') IS NOT 'text' OR length(json_extract(body,'$.reason'))>8192
 OR json_type(body,'$.depends_on') IS NOT 'array' OR json_type(body,'$.args') IS NOT 'object'
 OR json_type(body,'$.kind') IS NOT 'text'
 OR json_extract(body,'$.kind') NOT IN('create_collection','add_members','remove_members','rename_collection','delete_collection','verify_state');
INSERT INTO invalid SELECT 'duplicate operation' FROM operations GROUP BY json_extract(body,'$.op_id') HAVING count(*)>1;
INSERT INTO invalid SELECT 'dependency' FROM operations o,json_each(o.body,'$.depends_on') d
 WHERE d.type<>'text' OR NOT EXISTS(SELECT 1 FROM operations p WHERE p.ordinal<o.ordinal AND json_extract(p.body,'$.op_id')=d.value);
INSERT INTO invalid SELECT 'duplicate dependency' FROM operations o,json_each(o.body,'$.depends_on') d GROUP BY o.ordinal,d.value HAVING count(*)>1;
INSERT INTO invalid SELECT 'state hash' FROM operations,json_each(operations.body) j WHERE j.key IN('before','after')
 AND (j.type NOT IN('text','null') OR (j.type='text' AND (length(j.value)<>64 OR j.value GLOB '*[^0-9a-f]*')));
INSERT INTO invalid SELECT 'creation/deletion precondition' FROM operations WHERE
 (json_extract(body,'$.kind')='create_collection' AND json_type(body,'$.before') IS NOT 'null')
 OR (json_extract(body,'$.kind')<>'create_collection' AND json_type(body,'$.before') IS NOT 'text')
 OR (json_extract(body,'$.kind')='delete_collection' AND json_type(body,'$.after') IS NOT 'null')
 OR (json_extract(body,'$.kind')<>'delete_collection' AND json_type(body,'$.after') IS NOT 'text');
INSERT INTO invalid SELECT 'name args' FROM operations WHERE json_extract(body,'$.kind') IN('create_collection','rename_collection') AND
 ((SELECT count(*) FROM json_each(body,'$.args'))<>1 OR json_type(body,'$.args.name') IS NOT 'text'
 OR length(json_extract(body,'$.args.name')) NOT BETWEEN 1 AND 1024);
INSERT INTO invalid SELECT 'member args' FROM operations WHERE json_extract(body,'$.kind') IN('add_members','remove_members') AND
 ((SELECT count(*) FROM json_each(body,'$.args'))<>1 OR json_type(body,'$.args.members') IS NOT 'array' OR json_array_length(body,'$.args.members')>200000);
INSERT INTO invalid SELECT 'empty args' FROM operations WHERE json_extract(body,'$.kind') IN('delete_collection','verify_state')
 AND (SELECT count(*) FROM json_each(body,'$.args'))<>0;
INSERT INTO invalid SELECT 'member identity' FROM operations o,json_each(o.body,'$.args.members') m
 WHERE m.type<>'text' OR length(m.value) NOT BETWEEN 1 AND 256;
INSERT INTO invalid SELECT 'duplicate member' FROM operations o,json_each(o.body,'$.args.members') m GROUP BY o.ordinal,m.value HAVING count(*)>1;
CREATE TEMP TABLE identifiers(value TEXT);
INSERT INTO identifiers SELECT j.value FROM req,json_each(req.body) j WHERE j.key IN('job_id','plan_id','library_uuid','capabilities_version');
INSERT INTO identifiers SELECT json_extract(body,'$.device.instance_id') FROM req;
INSERT INTO identifiers SELECT json_extract(body,'$.op_id') FROM operations UNION ALL SELECT json_extract(body,'$.collection_uuid') FROM operations;
INSERT INTO identifiers SELECT m.value FROM operations o,json_each(o.body,'$.args.members') m;
INSERT INTO invalid SELECT 'identifier separator' FROM identifiers WHERE instr(value,'/') OR instr(value,char(92));
INSERT INTO invalid WITH RECURSIVE controls(n) AS(SELECT 0 UNION ALL SELECT n+1 FROM controls WHERE n<31)
 SELECT 'identifier control character' FROM identifiers,controls WHERE instr(value,char(n));
INSERT INTO invalid WITH RECURSIVE controls(n) AS(SELECT 0 UNION ALL SELECT n+1 FROM controls WHERE n<31)
 SELECT 'name control character' FROM operations,controls WHERE instr(json_extract(body,'$.args.name'),char(n));
INSERT INTO invalid SELECT 'new books schema' FROM req WHERE
 (json_extract(body,'$.schema')='kc-edit-request/v1' AND json_type(body,'$.new_books') IS NOT NULL) OR
 (json_extract(body,'$.schema')='kc-edit-request/v2' AND (json_type(body,'$.new_books') IS NOT 'array' OR json_array_length(body,'$.new_books') NOT BETWEEN 1 AND 20000));
CREATE TEMP TABLE new_books AS SELECT j.value body FROM req,json_each(req.body,'$.new_books') j;
INSERT INTO invalid SELECT 'new book descriptor' FROM new_books WHERE json_type(body) IS NOT 'object'
 OR (SELECT count(*) FROM json_each(body))<>4 OR json_type(body,'$.alias') IS NOT 'text'
 OR json_extract(body,'$.alias') NOT GLOB 'kc-new-*' OR length(json_extract(body,'$.alias'))<>71
 OR substr(json_extract(body,'$.alias'),8) GLOB '*[^0-9a-f]*'
 OR json_type(body,'$.location') IS NOT 'text' OR json_extract(body,'$.location') NOT LIKE '/mnt/us/documents/%'
 OR instr(json_extract(body,'$.location'),'/../') OR instr(json_extract(body,'$.location'),char(10)) OR instr(json_extract(body,'$.location'),char(13))
 OR instr(json_extract(body,'$.location'),char(92)) OR instr(json_extract(body,'$.location'),char(0))
 OR json_type(body,'$.size') IS NOT 'integer' OR json_extract(body,'$.size')<0
 OR json_type(body,'$.sha256') IS NOT 'text' OR length(json_extract(body,'$.sha256'))<>64 OR json_extract(body,'$.sha256') GLOB '*[^0-9a-f]*';
INSERT INTO invalid SELECT 'duplicate new book alias' FROM new_books GROUP BY json_extract(body,'$.alias') HAVING count(*)<>1;
INSERT INTO invalid SELECT 'duplicate new book location' FROM new_books GROUP BY json_extract(body,'$.location') HAVING count(*)<>1;
INSERT INTO invalid SELECT 'undeclared new book' FROM operations o,json_each(o.body,'$.args.members') m
 WHERE m.value GLOB 'kc-new-*' AND NOT EXISTS(SELECT 1 FROM new_books b WHERE json_extract(b.body,'$.alias')=m.value);
SELECT CASE WHEN EXISTS(SELECT 1 FROM invalid) THEN 'INVALID:'||(SELECT reason FROM invalid LIMIT 1) ELSE 'VALID' END;
