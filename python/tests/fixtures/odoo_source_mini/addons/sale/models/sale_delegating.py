from odoo import fields, models


class SaleContract(models.Model):
    _name = "sale.contract"
    _description = "Contract"
    _inherits = {"res.partner": "partner_id"}

    partner_id = fields.Many2one("res.partner", required=True)
    contract_ref = fields.Char()
