"""Read-only private feedback analysis; never creates training labels automatically."""
import argparse
from collections import Counter,defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3

def summarize(rows):
    texts=defaultdict(set)
    for row in rows:texts[row['text_sha256']].add(row['label'])
    return dict(response_events=len(rows),unique_source_messages=len({(r['chat_id'],r['message_id']) for r in rows}),
                labels=dict(Counter(r['label'] for r in rows)),
                false_positive_reports=sum(r['reason']=='false_positive_after_deletion' for r in rows),
                conflicting_exact_text_hashes=sorted(k for k,v in texts.items() if len(v)>1),
                deletion_outcomes=dict(Counter(r['telegram_delete_status'] for r in rows)),
                selection='Only administrator-reviewed notifications; not overall model accuracy',
                automatic_training=False,conflicts_automatically_resolved=False)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    # Deliberately do not initialize Tickets: this analysis must never create/migrate the source.
    with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        rows=[dict(r) for r in c.execute('''SELECT f.label,f.reason,t.chat_id,t.message_id,t.state,e.kind,e.text_sha256
            FROM feedback_events f JOIN tickets t USING(token) JOIN ticket_evidence e USING(token) ORDER BY f.id''')]
    for row in rows:row['telegram_delete_status']='confirmed_deleted' if row['kind']=='deleted' or row['state']=='deleted' else row['state'] if row['state'] in {'processing','unknown','rejected'} else 'not_requested'
    report=summarize(rows)
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=True))
if __name__=='__main__':main()
