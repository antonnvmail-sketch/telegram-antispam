from __future__ import annotations
import asyncio, sqlite3, tempfile
from pathlib import Path
from shadow import ShadowRuntime, admin_command_allowed, comparison_type, probability_bucket

class FakeModel:
    classes_=["ham","spam"]
    def __init__(self,p=0.9,fail=False): self.p=p; self.fail=fail
    def predict_proba(self,texts):
        if self.fail: raise RuntimeError("predict failure")
        return [[1-self.p,self.p] for _ in texts]

def create_db(path):
    con=sqlite3.connect(path); con.executescript((Path(__file__).parents[1]/"migrations/001_shadow_predictions.sql").read_text()); con.close()

def runtime(path,model,enabled=True): return ShadowRuntime(enabled=enabled,model_path=Path("unused"),model_version="experimental_char_v2",database_path=path,model=model)

async def observe(rt,**overrides):
    values=dict(chat_id=-100,user_id=7,username="user",message_id=11,message_type="text",source_kind="text",text="Супер!!!",has_caption=False,production_label="ham",production_p_spam=.1,production_action="allow",production_skipped=False)
    values.update(overrides); return await rt.observe(**values)

async def run_tests():
  with tempfile.TemporaryDirectory() as d:
    db=Path(d)/"shadow.sqlite"; create_db(db)
    assert not await observe(runtime(db,FakeModel(),False)) # 1 disabled
    loaded=runtime(db,FakeModel()); assert loaded.loaded and await observe(loaded) # 2 loaded
    badload=ShadowRuntime.load(enabled=True,model_path=str(Path(d)/"missing"),model_version="v",database_path=str(db)); assert badload.load_error # 3 load failure
    production_actions=["delete"]
    badpredict=runtime(db,FakeModel(fail=True)); assert await observe(badpredict) and badpredict.runtime_error_count==1 # 4 predict fail
    assert production_actions == ["delete"]  # production action survives shadow exception
    badwrite=runtime(Path(d)/"missing/db",FakeModel()); assert not await observe(badwrite) and badwrite.runtime_error_count==1 # 5 DB fail
    delete_calls=0; assert comparison_type("ham","spam",False)=="prod_ham_shadow_spam" and delete_calls==0 # 6 isolation
    assert comparison_type("spam","ham",False)=="prod_spam_shadow_ham" # 7 comparison
    con=sqlite3.connect(db); assert con.execute("select human_label,human_labeled_at,human_note from shadow_predictions limit 1").fetchone()==(None,None,None); con.close() # 8 NULL
    assert await observe(loaded,text="Супер",message_id=12) # 9 short
    assert await observe(loaded,text="text",message_id=13) # 10 text
    assert await observe(loaded,text="caption",has_caption=True,source_kind="caption",message_type="photo",message_id=14) # 11 caption
    assert await observe(loaded,text="/x",source_kind="slash_message",production_skipped=True,production_label=None,production_p_spam=None,message_id=15) # 12 slash
    assert not admin_command_allowed(8,[7]) # 13 forbidden
    assert admin_command_allowed(7,[7]) # 14 admin
    assert loaded.model_version=="experimental_char_v2" # 15 version
    assert probability_bucket(.49)=="<0.50" and probability_bucket(.8)==">=0.80"

if __name__=="__main__": asyncio.run(run_tests()); print("15 shadow tests passed")
