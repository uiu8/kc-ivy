-- Context contains the persisted pending document, never a new desired target.
BEGIN;
CREATE TEMP TABLE pending AS SELECT body FROM context;
CREATE TEMP TABLE aliases AS SELECT json_extract(j.value,'$.alias') alias,json_extract(j.value,'$.uuid') uuid FROM pending,json_each(pending.body,'$.prepared.book_bindings') j;
CREATE TEMP TABLE op AS SELECT json_extract(body,'$.operation.collection_uuid') uuid,
 json_extract(body,'$.operation.kind') kind FROM pending;
CREATE TEMP TABLE current_collection AS SELECT e.p_uuid uuid,e.p_titles_0_nominal name FROM Entries e,op
 WHERE e.p_uuid=op.uuid AND e.p_type='Collection';
CREATE TEMP TABLE members AS SELECT i_member_uuid uuid FROM Collections,op WHERE i_collection_uuid=op.uuid;
CREATE TEMP TABLE expected_books AS SELECT j.value body FROM pending,json_each(pending.body,'$.prepared.expected_books') j;
CREATE TEMP TABLE actual_counts AS SELECT i_member_uuid uuid,count(*) n FROM Collections
 WHERE i_member_uuid IN(SELECT json_extract(body,'$.uuid') FROM expected_books) GROUP BY i_member_uuid;
CREATE UNIQUE INDEX count_uuid ON actual_counts(uuid);
SELECT json_object('state',json(CASE WHEN EXISTS(SELECT 1 FROM current_collection) THEN
 json_object('members',json((SELECT json_group_array(uuid) FROM(SELECT coalesce(a.alias,m.uuid) uuid FROM members m LEFT JOIN aliases a ON a.uuid=m.uuid ORDER BY 1))),
 'name',(SELECT name FROM current_collection)) ELSE 'null' END),
 'valid',CASE WHEN
 NOT EXISTS(SELECT 1 FROM expected_books b LEFT JOIN Entries e ON e.p_uuid=json_extract(b.body,'$.uuid')
 LEFT JOIN actual_counts a ON a.uuid=e.p_uuid
 WHERE e.p_uuid IS NULL OR e.p_type<>'Entry:Item' OR e.p_location IS NOT json_extract(b.body,'$.location')
 OR e.p_collectionCount IS NULL OR e.p_collectionCount<>coalesce(a.n,0)
 OR e.p_collectionCount<>json_extract(b.body,'$.expected_count'))
 AND NOT EXISTS(SELECT 1 FROM Entries e,op WHERE e.p_uuid=op.uuid AND e.p_type<>'Collection')
 AND ((SELECT kind FROM op)<>'delete_collection' OR NOT EXISTS(SELECT 1 FROM members))
 THEN 1 ELSE 0 END);
COMMIT;
