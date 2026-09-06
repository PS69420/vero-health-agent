import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.semantic_memory import LocalTfidfVectorStore, NullVectorStore, extract_patient_quote


class TestLocalTfidfVectorStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = LocalTfidfVectorStore(self.tmpdir / "semantic.json")

    def test_empty_patient_returns_no_results(self):
        self.assertEqual(self.store.search("p1", "mask leak"), [])

    def test_finds_most_relevant_document_by_content(self):
        self.store.add("p1", "call-1", "agent: how's it going\npatient: the mask keeps leaking air on me",
                        metadata={"barrier": "mask_leak", "date": "2026-01-01"})
        self.store.add("p1", "call-2", "agent: how's it going\npatient: I just keep forgetting to put it on",
                        metadata={"barrier": "forgetting", "date": "2026-02-01"})
        results = self.store.search("p1", "mask leaking air problem", top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["doc_id"], "call-1")
        self.assertEqual(results[0]["metadata"]["barrier"], "mask_leak")

    def test_results_scoped_to_one_patient(self):
        self.store.add("p1", "call-1", "patient: the mask leaks constantly", metadata={"barrier": "mask_leak"})
        self.store.add("p2", "call-2", "patient: the mask leaks constantly", metadata={"barrier": "mask_leak"})
        results = self.store.search("p1", "mask leak", top_k=5)
        self.assertEqual([r["doc_id"] for r in results], ["call-1"])

    def test_re_adding_same_doc_id_replaces_not_duplicates(self):
        self.store.add("p1", "call-1", "patient: original text about leaking", metadata={"v": 1})
        self.store.add("p1", "call-1", "patient: updated text about leaking", metadata={"v": 2})
        results = self.store.search("p1", "leaking", top_k=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["metadata"]["v"], 2)

    def test_persists_across_instances(self):
        self.store.add("p1", "call-1", "patient: forgetting to use the machine some nights", metadata={"barrier": "forgetting"})
        reopened = LocalTfidfVectorStore(self.tmpdir / "semantic.json")
        results = reopened.search("p1", "forgetting to use machine", top_k=1)
        self.assertEqual(len(results), 1)


class TestExtractPatientQuote(unittest.TestCase):
    def test_skips_greeting_confirmation_and_returns_the_real_complaint(self):
        transcript = (
            "agent: Hi, is this Clay? This is the care team calling.\n"
            "patient: Yes, this is Clay.\n"
            "agent: How's it been going?\n"
            "patient: Honestly the mask keeps leaking air and it wakes me up.\n"
        )
        self.assertEqual(extract_patient_quote(transcript), "Honestly the mask keeps leaking air and it wakes me up.")

    def test_returns_none_when_no_patient_line_present(self):
        self.assertIsNone(extract_patient_quote("agent: hello\nagent: anyone there?"))


class TestNullVectorStore(unittest.TestCase):
    def test_add_is_a_no_op_and_search_returns_empty(self):
        store = NullVectorStore()
        store.add("p1", "call-1", "anything")  # must not raise
        self.assertEqual(store.search("p1", "anything"), [])


if __name__ == "__main__":
    unittest.main()
