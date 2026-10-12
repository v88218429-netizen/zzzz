import sys
import types
import unittest

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    sys.modules["httpx"] = types.SimpleNamespace(
        AsyncClient=type("AsyncClient", (), {}),
        Client=type("Client", (), {}),
        Timeout=lambda *args, **kwargs: None,
    )

import github_finance_charges_sync as finance


def finance_row(**overrides):
    row = {
        "reportId": 100,
        "dateFrom": "2026-10-11",
        "dateTo": "2026-10-11",
        "createDate": "2026-10-12T03:00:00Z",
        "rrDate": "2026-10-11",
        "rrdId": 12345,
        "docTypeName": "Отчет",
        "sellerOperName": "Логистика",
        "penalty": 10.5,
        "deduction": None,
        "paidStorage": 0,
        "paidAcceptance": 0,
        "additionalPayment": None,
        "deliveryService": None,
        "forPay": None,
    }
    row.update(overrides)
    return row


class FinanceMissingValuesTest(unittest.TestCase):
    def test_missing_amounts_stay_blank_and_do_not_become_zero(self):
        row = finance._normalize("Саныч", finance_row())
        self.assertEqual(row[10], 10.5)
        self.assertIsNone(row[11])
        self.assertEqual(row[12:14], [0.0, 0.0])
        self.assertIsNone(row[14])
        self.assertIsNone(row[15], "partial charge components must not produce a false total")
        self.assertIsNone(row[28])
        self.assertIsNone(row[29])

        sheet_row = finance._to_penalty_sheet_row(row)
        self.assertIsNone(sheet_row[5], "penalty summary must not zero-fill unknown components")

    def test_only_explicit_nonzero_amounts_are_finance_charges(self):
        explicit_zeros = finance._normalize("Саныч", finance_row(
            penalty=0, deduction=0, paidStorage=0, paidAcceptance=0,
        ))
        self.assertFalse(finance._is_charge(explicit_zeros))

        charge = finance._normalize("Саныч", finance_row())
        self.assertTrue(finance._is_charge(charge))

        reimbursement = finance._normalize("Саныч", finance_row(
            penalty=0, deduction=0, paidStorage=0, paidAcceptance=0, additionalPayment=5,
        ))
        self.assertTrue(finance._is_charge(reimbursement), "non-zero additional payments must not disappear")

    def test_invalid_numeric_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid numeric value"):
            finance._normalize("Саныч", finance_row(penalty="not-a-number"))


if __name__ == "__main__":
    unittest.main()
