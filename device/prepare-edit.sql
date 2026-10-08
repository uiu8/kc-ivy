-- MAIN = cc.db, CLI opened -readonly. Context is a KC-owned TEMP table.
BEGIN;
CREATE TEMP TABLE aliases AS SELECT json_extract(j.value,'$.alias') alias,json_extract(j.value,'$.uuid') uuid FROM context,json_each(context.body,'$.book_bindings') j;
CREATE TEMP TABLE operation AS SELECT json_extract(body,'$.operation') body FROM context;
CREATE TEMP TABLE op AS SELECT json_extract(body,'$.kind') kind,
 json_extract(body,'$.collection_uuid') uuid,json_extract(body,'$.args.name') name FROM operation;
CREATE TEMP TABLE current_collection AS
SELECT e.p_uuid uuid,e.p_titles_0_nominal name FROM Entries e,op
WHERE e.p_uuid=op.uuid AND e.p_type='Collection';
CREATE TEMP TABLE old_members(uuid TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO old_members SELECT DISTINCT i_member_uuid FROM Collections,op WHERE i_collection_uuid=op.uuid;
CREATE TEMP TABLE requested(uuid TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO requested SELECT coalesce(a.uuid,j.value) FROM operation,json_each(operation.body,'$.args.members') j LEFT JOIN aliases a ON a.alias=j.value;
CREATE TEMP TABLE target(uuid TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO target
SELECT uuid FROM old_members WHERE (SELECT kind FROM op) NOT IN ('delete_collection','remove_members')
UNION SELECT uuid FROM old_members WHERE (SELECT kind FROM op)='remove_members' AND uuid NOT IN (SELECT uuid FROM requested)
UNION SELECT uuid FROM requested WHERE (SELECT kind FROM op)='add_members';
CREATE TEMP TABLE changed(uuid TEXT PRIMARY KEY,delta INTEGER) WITHOUT ROWID;
INSERT INTO changed SELECT uuid,1 FROM target WHERE uuid NOT IN(SELECT uuid FROM old_members)
UNION ALL SELECT uuid,-1 FROM old_members WHERE uuid NOT IN(SELECT uuid FROM target);
INSERT INTO changed SELECT uuid,0 FROM old_members WHERE (SELECT kind FROM op)='rename_collection';
CREATE TEMP TABLE actual_counts AS SELECT i_member_uuid uuid,count(*) n FROM Collections
WHERE i_member_uuid IN(SELECT uuid FROM changed) GROUP BY i_member_uuid;
CREATE UNIQUE INDEX actual_count_uuid ON actual_counts(uuid);
CREATE TEMP TABLE expected_books AS
SELECT e.p_uuid uuid,e.p_location location,coalesce(a.n,0)+c.delta expected_count
FROM changed c JOIN Entries e ON e.p_uuid=c.uuid LEFT JOIN actual_counts a ON a.uuid=c.uuid;
CREATE TEMP TABLE problem AS SELECT CASE
 WHEN EXISTS(SELECT 1 FROM Entries e,op WHERE e.p_uuid=op.uuid AND e.p_type<>'Collection') THEN 'UUID_IS_NOT_COLLECTION'
 WHEN (SELECT kind FROM op)='create_collection' AND EXISTS(SELECT 1 FROM Entries e,op WHERE e.p_uuid=op.uuid OR (e.p_type='Collection' AND e.p_titles_0_nominal=op.name)) THEN 'CREATE_CONFLICT'
 WHEN (SELECT kind FROM op)<>'create_collection' AND (SELECT count(*) FROM current_collection)<>1 THEN 'MISSING_COLLECTION'
 WHEN (SELECT kind FROM op)='rename_collection' AND EXISTS(SELECT 1 FROM Entries e,op WHERE e.p_type='Collection' AND e.p_uuid<>op.uuid AND e.p_titles_0_nominal=op.name) THEN 'NAME_CONFLICT'
 WHEN EXISTS(SELECT 1 FROM Collections c,op WHERE c.i_collection_uuid=op.uuid GROUP BY c.i_member_uuid HAVING count(*)<>1) THEN 'DUPLICATE_RELATION'
 WHEN EXISTS(SELECT 1 FROM old_members m LEFT JOIN Entries e ON e.p_uuid=m.uuid WHERE e.p_uuid IS NULL OR e.p_type<>'Entry:Item') THEN 'UNKNOWN_MEMBER'
 WHEN EXISTS(SELECT 1 FROM requested m LEFT JOIN Entries e ON e.p_uuid=m.uuid WHERE e.p_uuid IS NULL OR e.p_type<>'Entry:Item') THEN 'UNKNOWN_REQUESTED_BOOK'
 WHEN EXISTS(SELECT 1 FROM changed c JOIN Entries e ON e.p_uuid=c.uuid LEFT JOIN actual_counts a ON a.uuid=c.uuid WHERE e.p_collectionCount IS NULL OR e.p_collectionCount<>coalesce(a.n,0)) THEN 'BEFORE_COUNTS_INCONSISTENT'
 WHEN EXISTS(SELECT 1 FROM expected_books WHERE instr(location,char(10)) OR instr(location,char(13))) THEN 'UNVERIFIABLE_FILE_PATH'
 WHEN (SELECT count(*) FROM target)>json_extract((SELECT body FROM context),'$.policy.max_members') AND (SELECT kind FROM op) IN('create_collection','add_members','remove_members') THEN 'MEMBER_LIMIT'
 ELSE 'READY' END status;
CREATE TEMP TABLE states AS SELECT
 CASE WHEN EXISTS(SELECT 1 FROM current_collection) THEN json_object('members',json((SELECT json_group_array(uuid) FROM(SELECT coalesce(a.alias,m.uuid) uuid FROM old_members m LEFT JOIN aliases a ON a.uuid=m.uuid ORDER BY 1))),
 'name',(SELECT name FROM current_collection)) ELSE 'null' END before_json,
 CASE WHEN (SELECT kind FROM op)='delete_collection' THEN 'null' ELSE json_object('members',json((SELECT json_group_array(uuid) FROM(SELECT coalesce(a.alias,m.uuid) uuid FROM target m LEFT JOIN aliases a ON a.uuid=m.uuid ORDER BY 1))),
 'name',CASE WHEN (SELECT kind FROM op) IN('create_collection','rename_collection') THEN (SELECT name FROM op) ELSE (SELECT name FROM current_collection) END) END after_json;
CREATE TEMP TABLE commands(ord INTEGER,sort_key TEXT,body TEXT);
INSERT INTO commands SELECT 0,'',json_object('insert',json_object('type','Collection','uuid',uuid,'lastAccess',CAST(strftime('%s','now') AS INTEGER),
 'titles',json_array(json_object('display',name,'direction','LTR','language','en_US')),
 'isVisibleInHome',1,'isArchived',1,'mimeType','application/x-kindle-collection','collections',NULL,
 'collectionCount',NULL,'collectionDataSetName',uuid)) FROM op WHERE kind='create_collection';
INSERT INTO commands SELECT 1,'',json_object('update',json_object('type','Collection','uuid',uuid,
 'members',json((SELECT json_group_array(uuid) FROM(SELECT uuid FROM target ORDER BY uuid))))) FROM op WHERE kind IN('create_collection','add_members','remove_members');
INSERT INTO commands SELECT 1,'',json_object('update',json_object('type','Collection','uuid',uuid,
 'titles',json_array(json_object('display',name,'direction','LTR','language','en_US')))) FROM op WHERE kind='rename_collection';
INSERT INTO commands SELECT 1,'',json_object('delete',json_object('uuid',uuid)) FROM op WHERE kind='delete_collection';
INSERT INTO commands SELECT 2,uuid,json_object('update',json_object('type','Entry:Item','uuid',uuid,'collectionCount',expected_count)) FROM expected_books WHERE uuid IN(SELECT uuid FROM changed WHERE delta<>0);
SELECT json_object('book_bindings',json((SELECT json_group_array(json_object('alias',alias,'uuid',uuid)) FROM aliases)), 'status',(SELECT status FROM problem),
 'before',json((SELECT before_json FROM states)),'after',json((SELECT after_json FROM states)),
 'expected_books',json((SELECT json_group_array(json_object('uuid',uuid,'location',location,'expected_count',expected_count)) FROM expected_books)),
 'payload',json_object('commands',json((SELECT json_group_array(json(body)) FROM(SELECT body FROM commands ORDER BY ord,sort_key))),'type','ChangeRequest','id',1));
COMMIT;
