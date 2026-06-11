from odoo import api, fields, models


class SaleOrder(models.Model):
    _name = "sale.order"
    _description = "Sales Order"

    name = fields.Char(required=True)
    partner_id = fields.Many2one("res.partner", string="Customer")
    company_id = fields.Many2one(comodel_name="res.company")
    line_ids = fields.One2many("sale.order.line", "order_id")
    amount_total = fields.Monetary()


class SaleOrderLine(models.Model):
    _name = "sale.order.line"
    _description = "Sales Order Line"

    order_id = fields.Many2one("sale.order")
    price_unit = fields.Float()


class SaleMixin(models.AbstractModel):
    _name = "sale.mixin"
    _description = "Mixin"

    note = fields.Text()
