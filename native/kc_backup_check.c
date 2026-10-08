/* KC 0.6.4: dedicated, read-only quick_check. No extension loading, arbitrary
 * SQL, database repairs or native Kindle edits. ICU comparisons fail closed. */
#include "sqlite3.h"
#include <stdio.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif

typedef struct { sqlite3 *db; int compared, failed, timed_out; clock_t start; } Guard;
static int refuse_compare(void *ctx, int na, const void *pa, int nb, const void *pb) {
    Guard *g=(Guard *)ctx;
    int n=na<nb?na:nb, rc;
    g->compared=1;
    sqlite3_interrupt(g->db);
    rc=memcmp(pa,pb,(size_t)n);
    return rc ? rc : (na>nb)-(na<nb);
}
static void needed(void *ctx, sqlite3 *db, int encoding, const char *name) {
    Guard *g=(Guard *)ctx;
    (void)encoding;
    if(sqlite3_stricmp(name,"icu")==0 &&
       sqlite3_create_collation(db,name,SQLITE_UTF8,g,refuse_compare)!=SQLITE_OK) g->failed=1;
}
static int progress(void *ctx) {
    Guard *g=(Guard *)ctx;
    if(g->compared || g->failed) return 1;
    if((double)(clock()-g->start)/CLOCKS_PER_SEC > 30.0) { g->timed_out=1; return 1; }
    return 0;
}
static int exactly(Guard *g, const char *sql, const char *expected) {
    sqlite3_stmt *s=0;
    int rc=sqlite3_prepare_v2(g->db,sql,-1,&s,0), ok=0;
    if(rc==SQLITE_OK && sqlite3_step(s)==SQLITE_ROW) {
        const unsigned char *v=sqlite3_column_text(s,0);
        if(v && strcmp((const char *)v,expected)==0 && sqlite3_step(s)==SQLITE_DONE) ok=1;
    }
    if(!ok) fprintf(stderr,"KC_CHECK: statement failed or unexpected result: %s\n",sqlite3_errmsg(g->db));
    if(sqlite3_finalize(s)!=SQLITE_OK) ok=0;
    return ok && !g->compared && !g->failed && !g->timed_out;
}
int main(int argc, char **argv) {
    Guard g={0}; int rc=1, selftest;
#ifdef _WIN32
    _setmode(_fileno(stdout),_O_BINARY);
    _setmode(_fileno(stderr),_O_BINARY);
#endif
    if(argc!=2) { fputs("Usage: kc-backup-check BACKUP_FILE | --self-test\n",stderr); return 2; }
    selftest=strcmp(argv[1],"--self-test")==0;
    if(!selftest && (!argv[1][0] || argv[1][0]=='-' || strcmp(argv[1],":memory:")==0 || strncmp(argv[1],"file:",5)==0)) {
        fputs("KC_CHECK: expected a regular backup file path\n",stderr); return 2;
    }
    g.start=clock();
    if(sqlite3_open_v2(selftest?":memory:":argv[1],&g.db,
        selftest?(SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE):SQLITE_OPEN_READONLY,0)!=SQLITE_OK) goto done;
    if(!selftest && sqlite3_db_readonly(g.db,"main")!=1) goto done;
    sqlite3_busy_timeout(g.db,1500);
    sqlite3_progress_handler(g.db,10000,progress,&g);
    if(sqlite3_collation_needed(g.db,&g,needed)!=SQLITE_OK) goto done;
    if(selftest) {
        /* Exercise actual comparator refusal, including its sticky guard. */
        int accepted=exactly(&g,"SELECT 'a' < 'b' COLLATE icu;","1");
        if(!accepted && g.compared && !g.failed) { puts("KC_CHECK_SELFTEST_OK"); rc=0; }
        goto done;
    }
    /* Cache target 512 KiB, no mmap, no repair, one consistent read transaction. */
    if(sqlite3_exec(g.db,"PRAGMA query_only=ON; PRAGMA cache_size=-512; PRAGMA mmap_size=0; BEGIN;",0,0,0)!=SQLITE_OK) goto done;
    if(!exactly(&g,"SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('Entries','Collections');","2")) goto done;
    if(!exactly(&g,"PRAGMA quick_check;","ok")) goto done;
    if(sqlite3_exec(g.db,"COMMIT;",0,0,0)!=SQLITE_OK) goto done;
    puts("KC_SCHEMA_OK\nok\nKC_BACKUP_CHECK_OK");
    rc=0;
done:
    if(rc) fprintf(stderr,"KC_CHECK: backup NOT validated: %s; ICU_compare=%d registration_error=%d cpu_budget_exceeded=%d\n",
        g.db?sqlite3_errmsg(g.db):"open failed",g.compared,g.failed,g.timed_out);
    if(g.db && sqlite3_close(g.db)!=SQLITE_OK) rc=1;
    return rc;
}
