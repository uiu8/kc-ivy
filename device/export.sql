-- Main database is ALWAYS opened -readonly. All analysis objects are TEMP.
-- run_context(body) is supplied by the fixed shell launcher, never by a task.
BEGIN;
CREATE TEMP TABLE context AS SELECT body FROM run_context;
-- Keep large JSON out of SQLite TEMP files. Read only our immutable private copy.
CREATE TEMP TABLE metadata_flags AS
SELECT json_type(CAST(readfile(json_extract(body,'$.metadata_path')) AS TEXT))='array' is_array FROM context;
CREATE TEMP TABLE metadata AS
SELECT '/mnt/us/' || replace(json_extract(j.value,'$.lpath'),char(92),'/') location,
       json_extract(j.value,'$.uuid') calibre_uuid
FROM context c, metadata_flags,
 json_each(CASE WHEN is_array THEN CAST(readfile(json_extract(c.body,'$.metadata_path')) AS TEXT) ELSE '[]' END) j
WHERE j.type='object' AND json_type(j.value,'$.lpath')='text'
 AND json_type(j.value,'$.uuid')='text' AND length(json_extract(j.value,'$.uuid'))>0;
CREATE INDEX metadata_location ON metadata(location);
CREATE TEMP TABLE mapped AS
SELECT location,count(DISTINCT calibre_uuid) n,
       CASE WHEN count(DISTINCT calibre_uuid)=1 THEN min(calibre_uuid) END calibre_uuid
FROM metadata GROUP BY location;
CREATE UNIQUE INDEX mapped_location ON mapped(location);
CREATE TEMP TABLE books AS
SELECT e.p_uuid uuid,e.p_titles_0_nominal title,e.p_location location,
       e.p_cdeKey cde_key,e.p_cdeType cde_type,e.p_collectionCount collection_count,
       coalesce(m.n,0) metadata_matches,m.calibre_uuid
FROM Entries e LEFT JOIN mapped m ON m.location=e.p_location WHERE e.p_type='Entry:Item';
CREATE UNIQUE INDEX books_uuid ON books(uuid);
CREATE TEMP TABLE relations AS
SELECT i_collection_uuid collection_uuid,i_member_uuid book_uuid,
       i_member_cde_type member_type,i_member_cde_key member_key,
       i_member_is_present member_present,i_is_sideloaded sideloaded,i_order AS ord
FROM Collections;
CREATE INDEX relations_collection ON relations(collection_uuid,book_uuid,ord);
CREATE TEMP TABLE incomplete AS
SELECT DISTINCT r.collection_uuid FROM relations r LEFT JOIN books b ON b.uuid=r.book_uuid
WHERE b.uuid IS NULL
UNION SELECT collection_uuid FROM relations GROUP BY collection_uuid,book_uuid HAVING count(*)<>1;
CREATE UNIQUE INDEX incomplete_uuid ON incomplete(collection_uuid);
CREATE TEMP TABLE shelves AS
SELECT e.p_uuid uuid,e.p_titles_0_nominal name,i.collection_uuid IS NULL complete
FROM Entries e LEFT JOIN incomplete i ON i.collection_uuid=e.p_uuid WHERE e.p_type='Collection';
SELECT json_object(
 'schema','kc-bookshelf-export/v2',
 'snapshot_id',json_extract(c.body,'$.snapshot_id'),
 'generated_utc',strftime('%Y-%m-%dT%H:%M:%SZ','now'),
 'device',json(json_extract(c.body,'$.device')),
 'firmware',json_extract(c.body,'$.firmware'),
 'capabilities',json(coalesce(json_extract(c.body,'$.capabilities'),json_object('version','kc-0.5.0-dev-readonly',
   'verified_operations',json('[]'),
   'evidence','Read-only export only. Native edit adapter has not passed device acceptance.'))),
 'policy',json(json_extract(c.body,'$.policy')),
 'mapping',json_object('stable',json(CASE WHEN json_extract(c.body,'$.mapping_stable')=1
   AND (SELECT is_array FROM metadata_flags) THEN 'true' ELSE 'false' END),
   'calibre_library_uuid',json_extract(c.body,'$.library_uuid'),
   'metadata_sha256',json_extract(c.body,'$.metadata_sha256')),
 'books',json((SELECT json_group_array(json_object('uuid',uuid,'title',title,'location',location,
   'cde_key',cde_key,'cde_type',cde_type,'collection_count',collection_count,
   'calibre_uuid',calibre_uuid,'metadata_matches',metadata_matches)) FROM (SELECT * FROM books ORDER BY uuid))),
 'collections',json((SELECT json_group_array(json_object('uuid',uuid,'name',coalesce(name,''),
   'complete',json(CASE WHEN complete THEN 'true' ELSE 'false' END))) FROM (SELECT * FROM shelves ORDER BY uuid))),
 'relations',json((SELECT json_group_array(json_object('collection_uuid',collection_uuid,
   'book_uuid',book_uuid,'member_type',member_type,'member_key',member_key,
   'member_present',member_present,'sideloaded',sideloaded,'order',ord))
   FROM (SELECT * FROM relations ORDER BY collection_uuid,book_uuid,ord)))
) FROM context c;
COMMIT;
