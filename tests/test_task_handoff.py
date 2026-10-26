import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from agent_platform.task_handoff import validate
from tests import test_business_library_api as dossier_tests
from ui import server


def draft():
    return {"schema":1,"plan_id":"a"*32,"steps":[{"service_id":"sales-review","request":"复盘本周客户"},
            {"service_id":"presentation-studio","request":"根据结果制作8页PPT"}], "step":0,"scope_id":"",
            "brief":"受众：管理层；重点：交付风险", "previous_result":"", "previous":None}


class TaskHandoffTests(unittest.TestCase):
    def validate(self, value, tasks, service="sales-review-readonly", scope=""):
        return validate(value,service_id=service,entity_id=scope,project_id="project-default",profile_id="sales-director",tasks=tasks)

    def test_shape_limits_scope_and_service_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); value=draft()
            self.assertEqual(self.validate(value,path),value)
            changed=self.validate(value,path);changed["steps"][0]["request"]="changed"
            self.assertNotEqual(value["steps"],changed["steps"])
            for key, replacement in (("schema",True),("step",True),("step",2),("plan_id","../bad"),("scope_id","other"),
                                     ("brief","a"*6001),("brief","\ud800"),("previous_result","not from first step"),
                                     ("steps",[]),("steps",[{"service_id":[],"request":"x"}]),
                                     ("steps",[{"service_id":"wechat-review","request":"x"}]),
                                     ("steps",[{"service_id":"sales-review","request":"x"*1001}])):
                bad=copy.deepcopy(value);bad[key]=replacement
                with self.subTest(key=key),self.assertRaises(ValueError):self.validate(bad,path)
            with self.assertRaises(ValueError):self.validate(value,path,"pdf-import")
            bad=copy.deepcopy(value);bad["hidden_context"]="unreviewed"
            with self.assertRaises(ValueError):self.validate(bad,path)

    def test_only_completed_same_plan_scope_version_predecessor_can_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);first=draft()
            source={"task_id":"request-fixture","status":"completed","version":3,"profile_id":"sales-director",
                    "project_id":"project-default","handoff":first}
            next_step={**copy.deepcopy(first),"step":1,"previous":{"task_id":"request-fixture","version":3},"previous_result":"已核对：需先确认交付日期"}
            def write(value): (path/"request-fixture.json").write_text(json.dumps(value),encoding="utf-8")
            write(source)
            self.assertEqual(self.validate(next_step,path,"presentation-studio-quick"),next_step)
            for key,val in (("status","failed"),("status","running"),("version",4),("profile_id","other"),("project_id","other"),
                            ("superseded_by_task_id","new"),("handoff",None),("handoff",{"plan_id":"b"*32}),
                            ("handoff",{**first,"step":1}),("handoff",{**first,"scope_id":"other"})):
                write({**source,key:val})
                with self.subTest(key=key),self.assertRaises(ValueError):self.validate(next_step,path,"presentation-studio")
            write([])
            with self.assertRaises(ValueError):self.validate(next_step,path,"presentation-studio")
            write(source)
            next_step["previous"]["task_id"]="../request-fixture"
            with self.assertRaises(ValueError):self.validate(next_step,path,"presentation-studio")

    def test_http_confirmation_persistence_duplicate_retry_and_snapshot_recovery(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root=Path(directory);requests=root/"requests";tasks=root/"tasks"
            profile={"id":"sales-director","services":[{"id":"sales-review-readonly","workflow":"sales.review"}]}
            for name,value in {"ROOT":root,"REQUESTS":requests,"TASKS":tasks}.items():stack.enter_context(patch.object(server,name,value))
            for name,value in {"profiles":[profile],"active_project":{},"route_new_service":profile["services"][0],"task_runtime_selection":{}}.items():
                stack.enter_context(patch.object(server,name,return_value=value))
            payload={"profile_id":"sales-director","service_id":"sales-review-readonly","project_id":"project-default",
                     "request":"复盘客户", "handoff":draft(),"handoff_confirmed":True}
            helper=dossier_tests.DossierApiTests()
            denied=helper.handler("/api/task-requests",payload,token=False);denied._do_POST();self.assertEqual(denied.replies[0][0],403)
            missing=helper.handler("/api/task-requests",{**payload,"handoff_confirmed":False});missing._do_POST()
            self.assertEqual(missing.replies[0][0],400);self.assertFalse(list(requests.glob("*.json")))
            for expected in (201,200):
                handler=helper.handler("/api/task-requests",payload);handler._do_POST();self.assertEqual(handler.replies[0][0],expected)
                record=handler.replies[0][1];self.assertEqual(record["handoff"],payload["handoff"])
            self.assertEqual(len(list(requests.glob("*.json"))),1)
            self.assertEqual(server.pending_task_requests([])[0]["handoff"],payload["handoff"])
            payload["handoff"]["brief"]="changed after registration"
            conflict=helper.handler("/api/task-requests",payload);conflict._do_POST();self.assertEqual(conflict.replies[0][0],400)
            self.assertEqual(json.loads(next(requests.glob("*.json")).read_text(encoding="utf-8"))["handoff"]["brief"],draft()["brief"])
            payload["handoff"]={**draft(),"plan_id":"b"*32,"brief":"需求"*2000}
            large=helper.handler("/api/task-requests",payload);large._do_POST();self.assertEqual(large.replies[0][0],201)
            self.assertEqual(large.replies[0][1]["handoff"]["brief"],"需求"*2000)


if __name__=="__main__":unittest.main()
