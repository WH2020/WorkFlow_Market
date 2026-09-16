import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui import server
from ui.business_library_api import source_records, frozen_file
from agent_platform import business_library as store
from agent_platform.library_store import build_library_snapshot, update_metadata


class DossierApiTests(unittest.TestCase):
    def handler(self, path, payload=None, token=True):
        handler=object.__new__(server.ControlHandler)
        handler.path=path
        body=json.dumps(payload or {}).encode()
        handler.rfile=io.BytesIO(body)
        handler.headers={"Content-Type":"application/json","Content-Length":str(len(body)),"X-Director-Token":server.SERVER_TOKEN if token else "invalid"}
        handler.local_host=lambda:True
        handler.local_user=lambda:True
        handler.replies=[]
        handler.send_json=lambda status,data:handler.replies.append((status,data))
        handler.send_error=lambda status:handler.replies.append((status,{}))
        return handler

    def test_mutations_require_token_and_conflict_is_409(self):
        for token in [False,True]:
            h=self.handler('/api/business-library',{"operation":"save"},token)
            with patch.object(server,"business_library_mutation",side_effect=store.DossierError("CONFLICT","conflict")) as mutation:
                h._do_POST()
                self.assertEqual(h.replies[0][0],409 if token else 403)
                self.assertEqual(mutation.call_count,int(token))

    def test_platform_gate_hides_wechat_refs_and_prevents_association(self):
        h=self.handler('/api/business-library')
        with patch.object(server,"business_library_snapshot",return_value={"groups":{"wechat:private":"sales"},"links":{"wechat:private":["entity-x"],"file:f":[]}}), patch.object(server,"wechat_http_supported",return_value=False):
            h._do_GET()
            self.assertEqual(h.replies[0][1]["groups"],{})
            self.assertNotIn("wechat:private",h.replies[0][1]["links"])
            with self.assertRaises(store.DossierError):
                server.business_library_mutation({"operation":"link","sources":["wechat:private"]})

    def test_synthetic_default_project_timestamps_do_not_change_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            a=server.default_project();b={**a,"updated_at":"changed generated timestamp"}
            self.assertEqual(source_records(root,[a]),source_records(root,[b]))

    def test_task_context_is_server_resolved_and_frozen_without_writing_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"inputs").mkdir(); (root/"inputs/fixture.txt").write_text("synthetic",encoding="utf-8")
            data=store.mutate(root,{"confirmed":True,"revision":store.snapshot(root)["revision"],"operation":"save","type":"customer","fields":{"name":"合成客户"}})
            identity=data["saved_id"]
            payload={"profile_id":"sales-director","service_id":"sales-review-readonly","request":"复盘合成客户","project_id":"project-default", "library_selection":{"entity_id":identity,"revision":data["revision"],"file_ids":["library-fixture"]}}
            h=self.handler('/api/task-requests',payload)
            profile={"id":"sales-director","services":[{"id":"sales-review-readonly","workflow":"sales.readonly"}]}
            with patch.object(server,"ROOT",root),patch.object(server,"REQUESTS",root/"requests"),patch.object(server,"profiles",return_value=[profile]),patch.object(server,"active_project"),patch.object(server,"route_new_service",return_value=profile["services"][0]),patch.object(server,"task_runtime_selection",return_value={}),patch.object(server,"business_library_snapshot",return_value=data),patch.object(server,"library_snapshot",return_value={"entries":[{"library_id":"library-fixture","title":"合成文件","path":"inputs/fixture.txt","version":"1"}]}):
                h.create_request(payload)
                record=h.replies[0][1]
                self.assertEqual(record["library_context"]["snapshot"]["name"],"合成客户")
                self.assertEqual(record["library_context"]["files"][0]["path"],"inputs/fixture.txt")
                self.assertNotIn("library_selection",record)
                self.assertEqual(record["library_context"]["files"][0]["sha256"], hashlib.sha256(b"synthetic").hexdigest())
                payload["library_selection"]["revision"]="stale"
                with self.assertRaises(store.DossierError): h.create_request(payload)
                payload["library_selection"]={"file_ids":["nonexistent"]}
                with self.assertRaises(ValueError): h.create_request(payload)
                self.assertEqual(len(list((root/"requests").glob('*.json'))),1)

    def test_project_binding_and_file_scope_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=store.source_entity("project","workspace:project-other","合成项目",workspace_id="project-other",archived=False)
            view=store.snapshot(root,[source])
            payload={"profile_id":"sales-director","service_id":"fixture","request":"fixture","project_id":"project-default","library_selection":{"entity_id":source["id"],"revision":view["revision"],"file_ids":[]}}
            handler=self.handler('/api/task-requests',payload)
            with patch.object(server,"profiles",return_value=[{"id":"sales-director"}]),patch.object(server,"active_project"),patch.object(server,"route_new_service",return_value={"id":"fixture","workflow":"fixture"}),patch.object(server,"task_runtime_selection",return_value={}),patch.object(server,"business_library_snapshot",return_value=view):
                with self.assertRaisesRegex(ValueError,"空间不一致"): handler.create_request(payload)
            with self.assertRaisesRegex(ValueError,"其他项目"):
                frozen_file(root,{"project_id":"project-other"},"project-default")
            for path in ("../secret.pdf","data/wechat/private.pdf"):
                with self.assertRaises(ValueError): frozen_file(root,{"path":path},"project-default")

    def test_busy_store_has_retryable_http_error(self):
        handler=self.handler('/api/business-library')
        with patch.object(server,"business_library_mutation",side_effect=store.DossierError("STORE_BUSY","retry")):
            handler._do_POST()
        self.assertEqual(handler.replies[0][0],503)

    def test_catalog_labels_cannot_remove_physical_project_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows=[{"path":"inputs/projects/project-a/f.pdf","project_id":"project-a","name":"fixture"}]
            def entry():return build_library_snapshot(root,sources=[],project_files=rows,artifacts=[])["entries"][0]
            base=entry();update_metadata(root,base,{"library_id":base["library_id"],"expected_version":0,"project_id":""})
            current=entry();self.assertEqual(current["project_id"],"");self.assertEqual(current["origin_project_id"],"project-a")
            with self.assertRaisesRegex(ValueError,"原始归属"):
                frozen_file(root,current,"project-b")
            with self.assertRaisesRegex(ValueError,"原始路径"):
                frozen_file(root,{"path":rows[0]["path"],"project_id":""},"project-b")


if __name__=='__main__':unittest.main()
