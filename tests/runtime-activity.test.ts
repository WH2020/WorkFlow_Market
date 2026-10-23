import assert from "node:assert/strict";
import test from "node:test";
import { RuntimeActivityTracker, safeBatchProgress } from "../pi/extensions/runtime-activity.ts";

test("model error is provisional until settled; automatic retry can recover", () => {
  const tracker=new RuntimeActivityTracker();tracker.start("a");tracker.model("a");
  tracker.messageEnd("a","error");assert.equal(tracker.snapshot("a")?.phase,"retry_wait");
  tracker.model("a");tracker.messageEnd("a","stop");tracker.settled("a");
  assert.equal(tracker.snapshot("a")?.phase,"settled");assert.equal(tracker.snapshot("a")?.error_code,undefined);
  tracker.start("a");tracker.messageEnd("a","aborted");tracker.settled("a");
  assert.equal(tracker.snapshot("a")?.phase,"error");assert.equal(tracker.snapshot("a")?.error_code,"MODEL_ABORTED");
});
test("only known in-flight WeChat tools publish bounded counters, never arbitrary content",()=>{
  const tracker=new RuntimeActivityTracker();tracker.start("a");tracker.toolStart("a","call","director_wechat_read");
  const progress={phase:"batch",current:3,total:12,messages:2500,level:0,secret:"PRIVATE_CANARY"};
  tracker.toolUpdate("a","unknown",progress);assert.equal(tracker.snapshot("a")?.batch,undefined);
  tracker.toolUpdate("a","call",progress);assert.equal(tracker.snapshot("a")?.batch?.current,3);
  assert.doesNotMatch(JSON.stringify(tracker.snapshot("a")),/PRIVATE_CANARY/);
  tracker.toolEnd("a","call",false);tracker.toolUpdate("a","call",progress);assert.equal(tracker.snapshot("a")?.batch,undefined);
  tracker.start("b");assert.equal(tracker.snapshot("a"),undefined);tracker.toolUpdate("b","call",progress);assert.equal(tracker.snapshot("b")?.batch,undefined);
  for(const value of [null,{}, {...progress,current:13},{...progress,total:129},{...progress,messages:NaN}])assert.equal(safeBatchProgress(value),undefined);
});
test("tool failure is visible but does not prevent model recovery; reads do not renew activity",()=>{
  let now="2026-09-17T01:00:00Z";const tracker=new RuntimeActivityTracker(()=>now);
  tracker.toolStart("a","call","any_tool");tracker.toolEnd("a","call",true);
  now="2026-09-17T01:01:00Z";assert.equal(tracker.snapshot("a")?.last_activity_at,"2026-09-17T01:00:00Z");
  tracker.settled("a");assert.equal(tracker.snapshot("a")?.error_code,"TOOL_ERROR");
  tracker.model("a");tracker.settled("a");assert.equal(tracker.snapshot("a")?.phase,"settled");
});
