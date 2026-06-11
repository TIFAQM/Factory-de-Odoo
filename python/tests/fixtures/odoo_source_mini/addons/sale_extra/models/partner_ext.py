from odoo import fields, models


class ResPartnerExt(models.Model):
    _name = "res.partner"
    _description = "Partner Extension"

    loyalty_points = fields.Integer()
