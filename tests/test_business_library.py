import concurrent.futures
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

from agent_platform import business_library as store


class DossierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = [store.source_entity("customer", "account:fixture:account-1", "合成客户", source_version="1"),
                        store.source_entity("project", "workspace:project-1", "合成项目", source_version="1", workspace_id="project-1", archived=False)]

    def view(self):
        return store.snapshot(self.root, self.sources)

    def mutate(self, **payload):
        return store.mutate(self.root, {"confirmed": True, "revision": self.view()["revision"], **payload}, sources=self.sources)

    def create(self, name="合成销售", kind="sales"):
        result = self.mutate(operation="save", type=kind, fields={"name": name})
        return next(e for e in result["entities"] if e["id"] == result["saved_id"])

    def test_read_is_nonmutating_and_first_failure_remains_readable(self):
        before = self.view()
        self.assertFalse((self.root / "data").exists())
        with self.assertRaises(store.DossierError):
            self.mutate(operation="save", type="sales", fields={"name": ""})
        self.assertEqual(self.view(), before)

    def test_overlay_persistence_identity_source_change_and_recovery(self):
        source = self.sources[0].copy()
        result = self.mutate(operation="save", id=source["id"], type="customer", fields={"name": "档案显示名", "stage": "跟进"})
        self.assertEqual(source, self.sources[0])
        self.assertEqual(self.view(), {k:v for k,v in result.items() if k not in {"saved_id", "message"}})
        self.sources[0]["source_version"] = "2"
        self.assertTrue(self.view()["entities"][0]["source_changed"])
        missing = store.snapshot(self.root, [])
        self.assertTrue(missing["entities"][0]["source_unavailable"])
        self.assertNotIn("source_unavailable", self.view()["entities"][0])

    def test_owner_contact_record_and_archive(self):
        seller = self.create()
        customer = self.sources[0]["id"]
        self.mutate(operation="save", id=customer, type="customer", fields={"owner": seller["id"]})
        self.mutate(operation="contact", id=customer, name="虚构联系人", role="技术", note="合成")
        result = self.mutate(operation="record", id=customer, kind="action", text="核对资料", due="2026-10-01", owner=seller["id"])
        record = next(e for e in result["entities"] if e["id"]==customer)["records"][0]
        self.mutate(operation="toggle", id=customer, record_id=record["id"], done=True)
        self.mutate(operation="archive", id=customer)
        with self.assertRaises(store.DossierError):
            self.mutate(operation="record", id=customer, kind="action", text="不应保存")
        self.mutate(operation="restore", id=customer)
        current = next(e for e in self.view()["entities"] if e["id"]==customer)
        self.assertEqual(len(current["contacts"]), 1)
        self.assertTrue(current["records"][0]["done"])
        self.assertEqual(current["owner"], seller["id"])
        self.assertGreaterEqual(len(current["history"]), 6)

    def test_source_updates_do_not_turn_inherited_values_into_overrides(self):
        source=self.sources[0];source["stage"]="A"
        self.mutate(operation="save",id=source["id"],type="customer",fields={"description":"local note"})
        source.update(stage="B",source_version="2")
        self.mutate(operation="save",id=source["id"],type="customer",fields={"description":"updated note"})
        item=self.view()["entities"][0]
        self.assertEqual(item["stage"],"B");self.assertTrue(item["source_changed"])
        self.mutate(operation="save",id=source["id"],type="customer",fields={"stage":"local stage"})
        source.update(stage="C",source_version="3")
        self.assertEqual(self.view()["entities"][0]["stage"],"local stage")
        self.assertTrue(self.view()["entities"][0]["source_changed"])

    def test_long_source_context_is_bounded(self):
        self.sources[0]["description"]="字"*2000
        context=store.task_context(self.view(),self.sources[0]["id"],self.view()["revision"])
        self.assertEqual(len(context["snapshot"]["description"]),1000)
        self.sources[0]["description"]="😀"*1000
        context=store.task_context(self.view(),self.sources[0]["id"],self.view()["revision"])
        self.assertEqual(len(context["snapshot"]["description"].encode("utf-16-le")),2000)

    def test_missing_source_archive_preserves_sparse_overrides(self):
        source=self.sources[0];source["stage"]="A"
        self.mutate(operation="save",id=source["id"],type="customer",fields={"description":"local"})
        sources=self.sources;self.sources=[]
        self.mutate(operation="archive",id=source["id"])
        source.update(stage="B",source_version="2");self.sources=sources
        item=self.view()["entities"][0]
        self.assertEqual(item["stage"],"B");self.assertEqual(item["description"],"local");self.assertTrue(item["archived"])

    def test_corrupt_store_is_rejected_without_mutation(self):
        self.create()
        with closing(sqlite3.connect(store._path(self.root))) as db:
            original=db.execute("SELECT payload FROM dossiers").fetchone()[0]
            changes=[("version","bad"),("links",{"file:x":"not-a-list"}),("groups",{"wechat:x":"invalid"}), ("entities",{"bad":{"id":"bad","type":"customer"}})]
            for key,value in changes:
                state=json.loads(original);state[key]=value
                encoded=json.dumps(state)
                db.execute("UPDATE dossiers SET payload=?",(encoded,));db.commit()
                with self.assertRaises(store.DossierError) as error:self.view()
                self.assertEqual(error.exception.code,"INVALID_STORE")
                self.assertEqual(db.execute("SELECT payload FROM dossiers").fetchone()[0],encoded)

    def test_locked_store_returns_retryable_error(self):
        self.create()
        with closing(sqlite3.connect(store._path(self.root))) as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(store.DossierError) as error:self.create("second")
            self.assertEqual(error.exception.code,"STORE_BUSY")

    def test_validation_duplicate_wrong_reference_and_workspace_identity(self):
        self.create()
        for payload in [dict(operation="save", type="sales", fields={"name":"合成销售"}),
                        dict(operation="save", type="project", fields={"name":"假项目空间"}),
                        dict(operation="save", type="customer", fields={"name":"C", "owner":"missing"}),
                        dict(operation="save", type="customer", fields={"name":"C", "workspace_id":"fake"}),
                        dict(operation="record", id=self.sources[0]["id"], kind="action", text="  "),
                        dict(operation="record", id=self.sources[0]["id"], kind="action", text="X", due="2026-02-30"),
                        dict(operation="archive", id=self.sources[1]["id"]),
                        dict(operation="save", id={}, type="sales", fields={"name":"S"})]:
            with self.subTest(payload=payload), self.assertRaises(store.DossierError):
                self.mutate(**payload)

    def test_snapshot_conflict_and_parallel_writers(self):
        revision = self.view()["revision"]
        def save(name):
            try:
                return store.mutate(self.root, {"confirmed":True,"revision":revision,"operation":"save","type":"sales","fields":{"name":name}}, sources=self.sources)
            except store.DossierError as e:
                return e.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["S1", "S2"]))
        self.assertEqual(sum(isinstance(r,dict) for r in results), 1)
        self.assertIn("CONFLICT", results)

    def test_atomic_multireference_links_and_classification(self):
        seller = self.create()["id"]
        customer = self.sources[0]["id"]
        def link(**payload):
            return store.mutate(self.root, {"confirmed":True,"revision":self.view()["revision"],**payload}, sources=self.sources, valid_sources={"file:one","wechat:two"})
        link(operation="link", sources=["file:one"], entity_ids=[customer])
        link(operation="link", sources=["file:one","wechat:two"], entity_ids=[seller], append=True)
        self.assertEqual(set(self.view()["links"]["file:one"]), {seller,customer})
        link(operation="entity-links", id=customer, sources=["file:one","wechat:two"], selected=["wechat:two"])
        self.assertEqual(self.view()["links"]["file:one"], [seller])
        link(operation="classify", sources=["wechat:two"], category="customer")
        self.assertEqual(self.view()["groups"]["wechat:two"], "customer")
        before = self.view()
        with self.assertRaises(store.DossierError):
            link(operation="link", sources=["file:one","file:absent"], entity_ids=[seller])
        self.assertEqual(self.view(), before)

    def test_confirmed_completed_task_writeback_is_once_and_scoped(self):
        identity = self.sources[0]["id"]
        context = store.task_context(self.view(), identity, self.view()["revision"])
        task = {"task_id":"request-fixture", "status":"completed", "library_context":context}
        payload = {"confirmed":True, "revision":self.view()["revision"],"operation":"record","id":identity,"kind":"action","text":"用户核对内容","task_id":task["task_id"]}
        with self.assertRaises(store.DossierError):
            store.mutate(self.root,payload,sources=self.sources,task={**task,"status":"running"})
        result = store.mutate(self.root,payload,sources=self.sources,task=task)
        payload["revision"] = result["revision"]
        with self.assertRaises(store.DossierError):
            store.mutate(self.root,payload,sources=self.sources,task=task)
        self.assertEqual(len(self.view()["entities"][0]["records"]),1)

    def test_no_confirmation_and_symlink_refused(self):
        with self.assertRaises(store.DossierError):
            store.mutate(self.root,{"operation":"save"})
        (self.root/"elsewhere").mkdir()
        try:
            (self.root/"data").symlink_to(self.root/"elsewhere", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation requires Windows privilege")
        with self.assertRaises(store.DossierError):
            self.view()

    def test_windows_junction_detection_does_not_depend_on_python_312(self):
        with patch.object(Path,"lstat",return_value=SimpleNamespace(st_mode=0o40755,st_file_attributes=0x400)):
            self.assertTrue(store.is_linklike(self.root/"data"))


if __name__ == "__main__":
    unittest.main()
