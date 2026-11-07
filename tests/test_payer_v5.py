"""Unit tests for model/v5-matching (payer_v5 + ar_payer decision rules). Synthetic data only, no real IDs.

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from collections import namedtuple
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import payer_v5 as P  # noqa: E402

Txn = namedtuple("Txn", "bank_txn_id entity booking_date amount currency counterparty_name counterparty_account text "
                        "ref2 remittance_info")


def txn(text="", cp="", iban="", day="2025-05-10", ent="ES01", amount=100.0, rmt=""):
    return Txn("BT-1", ent, day, amount, "EUR", cp, iban, text, "", rmt)


def customers(rows):
    cols = ["customer_id", "entity", "name", "trade_name", "iban", "group_id", "parent_customer_id"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols).fillna("")


def items(rows):
    cols = ["item_id", "item_type", "entity", "party_id", "party_name", "issue_date"]
    df = pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)
    df["item_type"] = df["item_type"].astype("category")
    return df


def ctx(cust, its, start="2025-04-01"):
    return SimpleNamespace(masters={"customers": cust}, items=its, root="",
                           bank=pd.DataFrame({"booking_date": [start]}))


# a pool of unrelated customers so that IDF behaves like on real data (common words are frequent)
FILLER = [(f"CF{i:04d}", "ES01", f"{w} {v} S.L.", "", "", "", "")
          for i, (w, v) in enumerate((w, v) for w in ("Jardines", "Piscinas", "Riegos", "Construcciones", "Servicios",
                                                      "Mantenimientos", "Suministros", "Instalaciones")
                                     for v in ("Norte", "Sur", "Este", "Oeste", "Centro", "Levante", "Galicia",
                                               "Castilla", "Rioja", "Aragon"))]


class Normalisation(unittest.TestCase):
    def test_accents_case_punctuation_legal_forms(self):
        self.assertEqual(P.split_name("Construcciones Martínez Industriales S.L.U."),
                         (("CONSTRUCCIONES", "MARTINEZ", "INDUSTRIALES"), ("SLU",)))
        self.assertEqual(P.split_name("Giardini Castelmora S.p.A.")[1], ("SPA",))
        self.assertEqual(P.split_name("Pool Supply Nowa Sp. z o.o.")[1], ("SP", "Z", "OO"))
        self.assertEqual(P.split_name("  hnos.   GARCÍA,  s.a. "), (("HERMANOS", "GARCIA"), ("SA",)))

    def test_name_word_that_is_also_a_legal_form_is_kept_inside_the_name(self):
        core, suf = P.split_name("Jackson & Peterson Spa Service Corp.")
        self.assertIn("SPA", core)
        self.assertEqual(suf, ("CORP",))

    def test_empty_and_missing(self):
        self.assertEqual(P.split_name(None), ((), ()))
        self.assertIsNone(P.alias_key(float("nan"), ""))


class Candidates(unittest.TestCase):
    def index(self, extra, its=None, history=None):
        c = ctx(customers(FILLER + extra), its if its is not None else items([]))
        return P.PayerIndex(c, history or {})

    def test_truncated_words(self):
        ix = self.index([("C1", "ES01", "Construcciones Martinez Industriales S.L.", "", "", "", ""),
                         ("C2", "ES01", "Martinez Jardines Sur S.L.", "", "", "", "")])
        res = ix.candidates(txn("TRANSFERENCIA DE CONSTR MARTINEZ IND | FRA 2025"))
        self.assertEqual(res[0][0], "C1")
        self.assertGreater(res[0][1], 0.8)

    def test_in_order_truncation_of_long_name(self):
        ix = self.index([("C1", "ES01", "Outdoor Living Hughes e Hijos S.L.", "", "", "", ""),
                         ("C2", "ES01", "Outdoor Living Costa S.L.", "", "", "", "")])
        res = ix.candidates(txn("DIRECT CREDIT OUTDOOR LIVING HUGHE INVOICE"))
        self.assertEqual(res[0][0], "C1")

    def test_legal_form_separates_lookalike_payers(self):
        ix = self.index([("C1", "ES01", "Landscapes Santa Lucia Springs LLC", "", "", "", ""),
                         ("C2", "ES01", "Landscapes Santa Lucia Springs Co.", "", "", "", "")])
        res = dict((p, s) for p, s, _ in ix.candidates(txn("ACH CREDIT", cp="Landscapes Santa Lucia Springs Co.")))
        self.assertGreater(res["C2"], res["C1"] + 0.3)

    def test_trade_name(self):
        ix = self.index([("C1", "ES01", "Grupo Garrido Riegos Integrales S.A.", "GGR Riegos", "", "", "")])
        self.assertEqual(ix.candidates(txn("TRANSFERENCIAS GGR RIEGOS | FRA"))[0][0], "C1")

    def test_renamed_company_via_older_documents(self):
        its = items([("ARI-1", "ARI", "ES01", "C1", "Aguas Viejas Montes S.L.", "2024-01-10")])
        ix = self.index([("C1", "ES01", "Nuevo Nombre Hidro S.L.", "", "", "", "")], its)
        self.assertEqual(ix.candidates(txn("TRANSFERENCIA DE AGUAS VIEJAS MONTES"))[0][0], "C1")

    def test_no_leakage_document_name_known_only_after_the_line(self):
        its = items([("ARI-1", "ARI", "ES01", "C1", "Aguas Viejas Montes S.L.", "2025-06-15")])
        ix = self.index([("C1", "ES01", "Nuevo Nombre Hidro S.L.", "", "", "", "")], its)
        self.assertEqual(ix.candidates(txn("TRANSFERENCIA DE AGUAS VIEJAS MONTES", day="2025-05-10")), [])

    def test_iban_history_without_name(self):
        hist = {"iban": {"DE89370400440532013000": {"C1": 9, "C2": 1}}}
        ix = self.index([("C1", "ES01", "Alfa Beta Gamma S.L.", "", "", "", ""),
                         ("C2", "ES01", "Delta Epsilon S.L.", "", "", "", "")], history=hist)
        res = ix.candidates(txn("SEPA-UEBERWEISUNG SVWZ+PAYMENT", iban="DE89370400440532013000"))
        self.assertEqual(res[0][0], "C1")
        self.assertAlmostEqual(res[0][2]["iban"], 0.9)

    def test_shared_master_iban_is_diluted(self):
        ix = self.index([("C1", "ES01", "Alfa Beta S.L.", "", "ES0012", "", ""),
                         ("C2", "ES01", "Gamma Delta S.L.", "", "ES0012", "", "")])
        res = ix.candidates(txn("TRANSFER", iban="ES0012"))
        self.assertTrue(all(abs(e["iban"] - 0.5) < 1e-9 for _, _, e in res))

    def test_payer_string_alias_history(self):
        hist = {"alias_key": {("ES01", "T:TRANSFERENCIAS ZZQ COMERCIAL"): {"C1": 4}}}
        ix = self.index([("C1", "ES01", "Distribuciones Quintana S.L.", "", "", "", "")], history=hist)
        res = ix.candidates(txn("TRANSFERENCIAS ZZQ COMERCIAL | 2025"))
        self.assertEqual(res[0][0], "C1")

    def test_other_entity_is_ignored(self):
        ix = self.index([("C1", "FR01", "Constructions Lefebvre SARL", "", "", "", "")])
        self.assertEqual(ix.candidates(txn("VIR DE CONSTRUCTIONS LEFEBVRE", ent="ES01")), [])

    def test_common_words_alone_are_not_enough(self):
        ix = self.index([])
        res = ix.candidates(txn("TRANSFERENCIA DE SERVICIOS | PAGO"))
        self.assertTrue(all(s < 0.55 for _, s, _ in res))

    def test_incomplete_line_does_not_crash(self):
        ix = self.index([("C1", "ES01", "Alfa Beta S.L.", "", "", "", "")])
        self.assertEqual(ix.candidates(Txn("BT", "ES01", "2025-05-01", 1.0, "EUR", float("nan"), float("nan"),
                                           float("nan"), float("nan"), float("nan"))), [])

    def test_determinism(self):
        ix = self.index([("C1", "ES01", "Construcciones Martinez Industriales S.L.", "", "", "", ""),
                         ("C2", "ES01", "Construcciones Martinez Integrales S.L.", "", "", "", "")])
        t = txn("TRANSFERENCIA DE CONSTRUCCIONES MARTINEZ IN")
        self.assertEqual(ix.candidates(t), ix.candidates(t))


class HistoryLeakage(unittest.TestCase):
    def test_history_ignores_lines_of_the_reconciled_period(self):
        import data as D
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "train"))
            pd.DataFrame({"item_id": ["ARI-1", "ARI-2"], "customer_id": ["C1", "C2"], "customer_name": ["A", "B"],
                          "entity": "ES01", "document_number": ["F1", "F2"], "issue_date": "2025-01-01",
                          "due_date": "2025-02-01", "currency": "EUR", "total_amount": "10", "payment_terms": "",
                          "customer_po": "", "payment_method": ""}).to_csv(
                os.path.join(root, "train", "ar_invoices.csv.gz"), index=False)
            bank = pd.DataFrame({"bank_txn_id": ["B1", "B2"], "entity": "ES01",
                                 "booking_date": ["2025-03-20", "2025-04-05"], "amount": ["10", "10"],
                                 "counterparty_name": ["", ""], "counterparty_account": ["IBANOLD", "IBANNEW"],
                                 "text": ["TRANSFER OLD", "TRANSFER NEW"], "orig_amount": ["", ""]})
            bank.to_csv(os.path.join(root, "train", "bank_transactions.csv.gz"), index=False)
            pd.DataFrame({"bank_txn_id": ["B1", "B2"], "item_id": ["ARI-1", "ARI-2"], "amount": ["10", "10"],
                          "category": "", "exception": "", "segment": "x", "difficulty": "1"}).to_csv(
                os.path.join(root, "train", "golden_allocations.csv.gz"), index=False)
            old = D.CACHE
            D.CACHE = os.path.join(root, "cache")
            try:
                c = SimpleNamespace(root=root, bank=pd.DataFrame({"booking_date": ["2025-04-01", "2025-04-30"]}))
                h = P.build_history(c)
            finally:
                D.CACHE = old
            self.assertIn("IBANOLD", h["iban"])
            self.assertNotIn("IBANNEW", h["iban"])  # booked on/after the phase start: never used


class DecisionRules(unittest.TestCase):
    def test_exact_kinds_only_for_non_top_payers(self):
        from passes import ar_payer as AP
        self.assertTrue(AP._kind_ok("single"))
        self.assertTrue(AP._kind_ok("ref_cn"))
        self.assertFalse(AP._kind_ok("subset_dfs"))
        self.assertFalse(AP._kind_ok("frac_partial"))


if __name__ == "__main__":
    unittest.main()


class Overpayment(unittest.TestCase):
    def setUp(self):
        from passes import ar_payer as AP
        self.AP = AP
        # item tuple layout of ar_index.ARIndex.item: (typ, ent, pid, doc, issue, due, cur, total, ...)
        self.m = SimpleNamespace(ix=SimpleNamespace(
            item={"I1": ("ARI", "ES01", "C1", "F1", "2025-04-01", "2025-05-01", "EUR", 1000.0),
                  "I2": ("ARI", "ES01", "C1", "F2", "2025-04-02", "2025-05-02", "EUR", 1300.0),
                  "I3": ("ARI", "ES01", "C1", "F3", "2025-06-20", "2025-07-20", "EUR", 950.0)},
            cust_items={"C1": ["I1", "I2", "I3"]}))
        self.ctx = SimpleNamespace(open={"I1": 1000.0, "I2": 1300.0, "I3": 950.0})

    def run_(self, amount, cands=(("C1", 1.2, {}),)):
        return self.AP.overpayment(self.m, self.ctx, txn(amount=amount, day="2025-05-10"), amount, list(cands))

    def test_round_excess(self):
        r = self.AP.round_excess
        self.assertTrue(r(100.0, 4898.67))
        self.assertTrue(r(10.0, 1000.0))
        self.assertTrue(r(71.03, 710.28))     # 10 %
        self.assertFalse(r(36.13, 15000.0))
        self.assertFalse(r(3.0, 1000.0))

    def test_single_invoice_round_excess(self):
        self.assertEqual(self.run_(1100.0), [{"item_id": "I1", "amount": 1000.0, "exception": "OVERPAYMENT"}])

    def test_arbitrary_excess_is_not_an_overpayment(self):
        self.assertIsNone(self.run_(1037.42))

    def test_invoice_issued_after_the_line_is_ignored(self):
        self.ctx.open = {"I3": 950.0}
        self.assertIsNone(self.run_(1000.0))

    def test_ambiguous_payer_or_two_invoices(self):
        self.assertIsNone(self.run_(1100.0, cands=(("C1", 1.2, {}), ("C2", 1.15, {}))))
        self.ctx.open = {"I1": 1000.0, "I2": 1050.0}
        self.assertIsNone(self.run_(1100.0))   # 1000 + 100 and 1050 + 50: both round -> no guess

    def test_weak_payer(self):
        self.assertIsNone(self.run_(1100.0, cands=(("C1", 0.7, {}),)))
