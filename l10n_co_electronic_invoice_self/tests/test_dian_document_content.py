# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from hashlib import sha384

from lxml import etree

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
}
PIN = "PIN-SECRET-4242"
TECHNICAL_KEY = "fc8eac422eba16e22ffd8c6f94b3f40a6e38162c"


@tagged("post_install", "-at_install")
class TestDianDocumentContent(AccountTestInvoicingCommon):
    """Generate real documents in demo mode and check their content."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("co")
    def setUpClass(cls):
        super().setUpClass()
        co = cls.env.ref("base.co")
        nit = cls.env.ref("l10n_co.rut")
        responsibility = cls.env["l10n_co.responsibility.type"].search([], limit=1)
        regimen = (
            cls.env["res.partner"]
            ._fields["l10n_co_regimen_fiscal"]
            ._description_selection(cls.env)[0][0]
        )
        partner_vals = {
            "country_id": co.id,
            "l10n_latam_identification_type_id": nit.id,
            "l10n_co_regimen_fiscal": regimen,
            "l10n_co_responsibility_ids": [(6, 0, responsibility.ids)],
            "is_company": True,
            "street": "Calle 1 # 2-3",
            "zip": "110111",
            "email": "test@example.com",
        }
        cls.company_data["company"].partner_id.write(
            dict(partner_vals, vat="900373115-3")
        )
        cls.company_data["company"].l10n_co_ciiu_id = cls.env["l10n_co.ciiu"].search(
            [], limit=1
        )
        cls.customer = cls.env["res.partner"].create(
            dict(partner_vals, name="Cliente Prueba SAS", vat="800197268-4")
        )
        cls.env.ref("uom.product_uom_unit").unece_code_id = cls.env[
            "unece.uom.code"
        ].search([("code", "=", "94")], limit=1)
        cls.tax_iva = cls.env["account.tax"].search(
            [
                ("company_id", "=", cls.company_data["company"].id),
                ("type_tax_use", "=", "sale"),
                ("amount_type", "=", "percent"),
                ("amount", "=", 19),
                ("l10n_co_tax_type_id.code", "=", "01"),
            ],
            limit=1,
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Servicio de desarrollo",
                "type": "service",
                "default_code": "SRV1",
                "taxes_id": [(6, 0, cls.tax_iva.ids)],
            }
        )
        cls.journal = cls.company_data["default_journal_sale"]
        if not cls.journal.l10n_latam_use_documents:
            cls.journal.l10n_latam_use_documents = True
        cls.journal.write(
            {
                "l10n_co_dian_operation_mode": "demo",
                "l10n_co_dian_software_identification": "demo-software-id",
                "l10n_co_dian_software_pin": PIN,
                "l10n_co_dian_software_technical_key_test": TECHNICAL_KEY,
                "l10n_co_electronic_document_resolution_test": "18760000001",
                "l10n_co_electronic_document_prefix_test": "SETP",
                "l10n_co_electronic_document_start_number_test": 990000000,
                "l10n_co_electronic_document_end_number_test": 995000000,
                "l10n_co_electronic_document_start_resolution_date_test": "2019-01-19",
                "l10n_co_electronic_document_end_resolution_date_test": "2030-01-19",
            }
        )
        cls.service = cls.env["l10n_co.dian.self.service"]

    def _create_posted_invoice(self):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "journal_id": self.journal.id,
                "l10n_latam_document_type_id": self.env["l10n_latam.document.type"]
                .search(
                    [("code", "=", "01"), ("country_id.code", "=", "CO")],
                    limit=1,
                )
                .id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "quantity": 3,
                            "price_unit": 1234567.89,
                            "tax_ids": [(6, 0, self.tax_iva.ids)],
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        return invoice

    def _create_posted_credit_note(self, invoice):
        invoice.l10n_co_edi_cufe_cude_ref = "a" * 96
        invoice.l10n_co_dian_status = "accepted"
        reversal = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create({"journal_id": self.journal.id, "reason": "Devolución"})
        )
        credit_note = self.env["account.move"].browse(reversal.refund_moves()["res_id"])
        credit_note.write(
            {
                "l10n_co_discrepancy_response_code": "nc_2",
                "l10n_latam_document_type_id": self.env["l10n_latam.document.type"]
                .search(
                    [("code", "=", "91"), ("country_id.code", "=", "CO")],
                    limit=1,
                )
                .id,
            }
        )
        credit_note.action_post()
        return credit_note

    def _signed_root(self, move):
        signed_xml, _signer = self.service._generate_signed_xml(move)
        return etree.fromstring(signed_xml)

    def test_invoice_xml_does_not_contain_technical_key(self):
        root = self._signed_root(self._create_posted_invoice())
        self.assertNotIn(TECHNICAL_KEY.encode(), etree.tostring(root))
        self.assertIsNone(root.find("cbc:Note", NS))

    def test_credit_note_xml_does_not_contain_pin(self):
        credit_note = self._create_posted_credit_note(self._create_posted_invoice())
        root = self._signed_root(credit_note)
        self.assertNotIn(PIN.encode(), etree.tostring(root))
        self.assertIsNone(root.find("cbc:Note", NS))

    def test_cufe_is_sha384_of_dian_fields(self):
        invoice = self._create_posted_invoice()
        root = self._signed_root(invoice)
        cufe_input = "".join(
            [
                root.findtext("cbc:ID", namespaces=NS),
                root.findtext("cbc:IssueDate", namespaces=NS),
                root.findtext("cbc:IssueTime", namespaces=NS),
                "3703703.67",
                "01",
                "703703.70",
                "04",
                "0.00",
                "03",
                "0.00",
                "4407407.37",
                "900373115",
                "800197268",
                TECHNICAL_KEY,
                "2",
            ]
        )
        self.assertEqual(
            root.findtext("cbc:UUID", namespaces=NS),
            sha384(cufe_input.encode()).hexdigest(),
        )

    def test_qr_reports_document_level_iva_once(self):
        root = self._signed_root(self._create_posted_invoice())
        qr_text = root.findtext(".//sts:QRCode", namespaces=NS)
        self.assertIn("ValIva: 703703.70\n", qr_text)
        self.assertIn("ValTolFac: 4407407.37\n", qr_text)
