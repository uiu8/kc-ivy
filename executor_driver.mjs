// Simulation only: the sole writer of the isolated fixture cc.db.
import fs from 'node:fs';
import path from 'node:path';
import {DatabaseSync} from 'node:sqlite';
const root=process.env.KC_EXECUTOR_FIXTURE;
if(!root || !fs.existsSync(path.join(root,'fixture.marker'))) throw Error('Missing isolated fixture marker');
const kind=process.argv[2],args=process.argv.slice(3);
if(kind==='uuid'){process.stdout.write(crypto.randomUUID()+'\n');process.exit(0);}
if(kind==='sleep'||kind==='sync'){
 fs.appendFileSync(path.join(root,'trace.jsonl'),JSON.stringify({kind,args})+'\n');process.exit(0);
}
if(kind!=='curl') throw Error('Unsupported fixture action');
const body=JSON.parse(fs.readFileSync(args[args.indexOf('--data-binary')+1].slice(1),'utf8'));
fs.appendFileSync(path.join(root,'trace.jsonl'),JSON.stringify({kind,body})+'\n');
const config=JSON.parse(fs.readFileSync(path.join(root,'options.json'),'utf8'));
if(config.no_apply_timeout){process.stdout.write('000');process.exit(28);}
const db=new DatabaseSync(path.join(root,'cc.db'));
db.exec('BEGIN');
for(const command of body.commands){
 if(command.insert){
  const v=command.insert;
  db.prepare("INSERT INTO Entries(p_uuid,p_type,p_titles_0_nominal) VALUES(?,'Collection',?)").run(v.uuid,v.titles[0].display);
 }else if(command.update){
  const v=command.update;
  if(v.type==='Collection'){
   if(v.titles) db.prepare('UPDATE Entries SET p_titles_0_nominal=? WHERE p_uuid=?').run(v.titles[0].display,v.uuid);
   if(v.members){
    db.prepare('DELETE FROM Collections WHERE i_collection_uuid=?').run(v.uuid);
    const add=db.prepare('INSERT INTO Collections VALUES(?,?,?,?,?,?,?)');
    for(const [i,bid] of v.members.entries()){
     const b=db.prepare('SELECT * FROM Entries WHERE p_uuid=?').get(bid);
     add.run(v.uuid,bid,b?.p_cdeType??null,b?.p_cdeKey??null,1,1,i);
    }
   }
  }else if(v.type==='Entry:Item'&&!config.omit_counts){
   db.prepare('UPDATE Entries SET p_collectionCount=? WHERE p_uuid=?').run(v.collectionCount,v.uuid);
  }
 }else if(command.delete){
  const id=command.delete.uuid;
  const e=db.prepare('SELECT p_type FROM Entries WHERE p_uuid=?').get(id);
  if(e?.p_type!=='Collection') throw Error('Attempt to delete a book');
  db.prepare('DELETE FROM Collections WHERE i_collection_uuid=?').run(id);
  db.prepare('DELETE FROM Entries WHERE p_uuid=?').run(id);
 }
}
db.exec('COMMIT');
if(config.resize_book){
 const book=db.prepare('SELECT p_location FROM Entries WHERE p_uuid=?').get(config.resize_book);
 if(!book || !path.resolve(book.p_location).startsWith(path.resolve(root)+path.sep)) throw Error('Invalid fixture book');
 fs.appendFileSync(book.p_location,'changed during fixture POST');
}
db.close();
fs.writeFileSync(args[args.indexOf('-o')+1],'fixture response');
process.stdout.write(config.timeout_applied?'000':'200');
process.exit(config.timeout_applied?28:0);
