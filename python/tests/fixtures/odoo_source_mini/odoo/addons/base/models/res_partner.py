from odoo import fields, models


class ResPartner(models.Model):
    _name = "res.partner"
    _description = "Contact"

    name = fields.Char()
    email = fields.Char()
