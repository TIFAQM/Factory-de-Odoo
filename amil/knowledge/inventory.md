# Odoo 17.0/18.0/19.0 Inventory & Warehouse (WMS) Rules

> Category: Inventory | Target: Odoo 17.0/18.0/19.0 | Load with: MASTER.md + inventory.md
>
> Covers stock.picking lifecycle, warehouse locations, lot/serial tracking, procurement
> rules, inventory adjustments, picking types, stock valuation, immediate transfers,
> back orders, multi-step routes, and stock availability checks. All patterns assume
> the `stock` module is installed and `depends` includes `"stock"`.

---

## Stock Picking Lifecycle

### Create pickings with move lines, not empty

**WRONG:**
```python
picking = self.env["stock.picking"].create({
    "partner_id": partner.id,
    "picking_type_id": picking_type.id,
    "location_id": src_location.id,
    "location_dest_id": dest_location.id,
})
# Picking created without any move lines -- nothing to process
```

**CORRECT:**
```python
picking = self.env["stock.picking"].create({
    "partner_id": partner.id,
    "picking_type_id": picking_type.id,
    "location_id": src_location.id,
    "location_dest_id": dest_location.id,
    "move_ids": [
        (0, 0, {
            "name": product.name,
            "product_id": product.id,
            "product_uom_qty": 10.0,
            "product_uom": product.uom_id.id,
            "location_id": src_location.id,
            "location_dest_id": dest_location.id,
        }),
    ],
})
```

**Why:** A picking without `move_ids` is an empty shell -- it cannot be confirmed or validated. Odoo expects at least one `stock.move` to define what products are being transferred. Creating moves separately and linking them afterward is fragile and can leave orphan records.

---

## Picking Confirmation

### Use the picking workflow methods, never set state directly

**WRONG:**
```python
picking.state = "done"  # Bypasses all validation, stock quants are NOT updated
```

**CORRECT:**
```python
picking.action_confirm()       # draft -> confirmed (checks availability)
picking.action_assign()        # confirmed -> assigned (reserves stock)
picking.button_validate()      # assigned -> done (moves stock, updates quants)
```

**Why:** Setting `state` directly skips the entire WMS workflow: no stock reservation, no quant updates, no accounting entries for valuation, no procurement triggers. The `action_confirm` / `action_assign` / `button_validate` chain ensures stock levels, lot tracking, valuation layers, and downstream pickings are all correctly updated.

---

## Warehouse Locations

### Use warehouse properties, never hardcode location IDs

**WRONG:**
```python
location_id = 8   # Hardcoded stock location ID -- breaks across databases
picking.write({"location_id": location_id})
```

**CORRECT:**
```python
warehouse = self.env["stock.warehouse"].search(
    [("company_id", "=", self.env.company.id)], limit=1
)
picking.write({"location_id": warehouse.lot_stock_id.id})
```

**Why:** Location IDs differ between databases, demo data, and multi-company setups. The `stock.warehouse` model provides stable references: `lot_stock_id` (stock), `wh_input_stock_loc_id` (input), `wh_output_stock_loc_id` (output), `wh_pack_stock_loc_id` (packing). Hardcoded IDs cause `MissingError` or silently point to wrong locations.

---

## Lot and Serial Tracking

### Use stock.move.line with lot_id, never create quants directly

**WRONG:**
```python
# Directly creating quants -- bypasses tracking, no audit trail
self.env["stock.quant"].create({
    "product_id": product.id,
    "location_id": location.id,
    "lot_id": lot.id,
    "quantity": 10.0,
})
```

**CORRECT:**
```python
lot = self.env["stock.lot"].create({
    "name": "LOT-001",
    "product_id": product.id,
    "company_id": self.env.company.id,
})
# Set lot on the move line during picking validation
for move_line in picking.move_line_ids:
    if move_line.product_id == product:
        move_line.write({
            "lot_id": lot.id,
            "quantity": move_line.quantity,
        })
picking.button_validate()
```

**Why:** Quants are an internal ledger managed by stock moves. Creating them directly bypasses serial number uniqueness checks, lot lifecycle tracking, and valuation layer creation. Always assign lots through `stock.move.line` during the picking workflow so Odoo reconciles quants automatically.

---

## Procurement Rules

### Use procurement.group, never create stock.move manually for replenishment

**WRONG:**
```python
# Manual move creation for replenishment -- ignores routes, rules, lead times
self.env["stock.move"].create({
    "name": "Manual replenishment",
    "product_id": product.id,
    "product_uom_qty": 50.0,
    "product_uom": product.uom_id.id,
    "location_id": supplier_location.id,
    "location_dest_id": stock_location.id,
})
```

**CORRECT:**
```python
procurement_group = self.env["procurement.group"]
values = {
    "warehouse_id": warehouse,
    "company_id": self.env.company,
}
procurement_group.run(
    [
        procurement_group.Procurement(
            product,
            50.0,
            product.uom_id,
            stock_location,
            "Replenishment",   # origin
            "Replenishment",   # name
            self.env.company,
            values,
        ),
    ],
)
```

**Why:** `procurement.group.run()` evaluates the product's routes, reordering rules, preferred vendors, and lead times to determine the correct action (buy, manufacture, or internal transfer). Manual `stock.move` creation skips route evaluation, ignores minimum order quantities, and does not trigger purchase orders or manufacturing orders.

---

## Inventory Adjustment

### Use the inventory adjustment action, never write quant quantities directly

**WRONG:**
```python
quant = self.env["stock.quant"].search([
    ("product_id", "=", product.id),
    ("location_id", "=", location.id),
])
quant.write({"quantity": 100.0})  # No audit trail, breaks valuation
```

**CORRECT:**
```python
quant = self.env["stock.quant"].search([
    ("product_id", "=", product.id),
    ("location_id", "=", location.id),
])
if not quant:
    quant = self.env["stock.quant"].create({
        "product_id": product.id,
        "location_id": location.id,
    })
quant.write({"inventory_quantity": 100.0})
quant.action_apply_inventory()
```

**Why:** Writing `quantity` directly bypasses the inventory adjustment workflow. Using `inventory_quantity` + `action_apply_inventory()` creates a proper inventory adjustment move with full audit trail, updates valuation layers, and records the adjustment in the stock move history. Direct writes leave no trace and corrupt the running stock valuation.

---

## Picking Types

### Use warehouse picking type references, never hardcode picking_type_id

**WRONG:**
```python
picking = self.env["stock.picking"].create({
    "picking_type_id": 1,  # Hardcoded -- which type is ID 1?
    "location_id": location.id,
    "location_dest_id": dest.id,
})
```

**CORRECT:**
```python
warehouse = self.env["stock.warehouse"].search(
    [("company_id", "=", self.env.company.id)], limit=1
)
# Use the appropriate picking type from the warehouse:
# warehouse.in_type_id    -- Receipts (incoming)
# warehouse.out_type_id   -- Delivery Orders (outgoing)
# warehouse.int_type_id   -- Internal Transfers
# warehouse.pick_type_id  -- Pick (multi-step)
# warehouse.pack_type_id  -- Pack (3-step)
picking = self.env["stock.picking"].create({
    "picking_type_id": warehouse.out_type_id.id,
    "location_id": warehouse.lot_stock_id.id,
    "location_dest_id": self.env.ref("stock.stock_location_customers").id,
    "move_ids": [(0, 0, {
        "name": product.name,
        "product_id": product.id,
        "product_uom_qty": 5.0,
        "product_uom": product.uom_id.id,
        "location_id": warehouse.lot_stock_id.id,
        "location_dest_id": self.env.ref("stock.stock_location_customers").id,
    })],
})
```

**Why:** Picking type IDs vary across databases and companies. The `stock.warehouse` model exposes typed references (`in_type_id`, `out_type_id`, `int_type_id`, `pick_type_id`, `pack_type_id`) that always resolve correctly. Hardcoded IDs cause pickings to use the wrong sequence, wrong default locations, or fail with `MissingError` on a fresh database.

---

## Stock Valuation

### Use stock.valuation.layer, never compute stock value manually

**WRONG:**
```python
# Manual valuation -- ignores FIFO/AVCO costing, currency, landed costs
total_value = product.qty_available * product.standard_price
```

**CORRECT:**
```python
valuation_layers = self.env["stock.valuation.layer"].search([
    ("product_id", "=", product.id),
    ("company_id", "=", self.env.company.id),
])
total_value = sum(valuation_layers.mapped("value"))
unit_cost = sum(valuation_layers.mapped("value")) / max(
    sum(valuation_layers.mapped("quantity")), 1
)
```

**Why:** `standard_price` is only accurate for Standard costing. For FIFO and Average (AVCO) costing methods, the true inventory value lives in `stock.valuation.layer` records that track each stock move's cost. Manual multiplication ignores landed costs, currency conversions, and partial shipments. The valuation layer is the single source of truth for inventory value.

---

## Immediate Transfer

### Use the stock.immediate.transfer wizard, never skip validation steps

**WRONG:**
```python
# Skipping the wizard -- quantities not set on move lines
picking.action_confirm()
picking.action_assign()
picking.write({"state": "done"})  # Bypasses quantity check entirely
```

**CORRECT:**
```python
picking.action_confirm()
picking.action_assign()
# Set done quantities on move lines
for move in picking.move_ids:
    move.quantity = move.product_uom_qty
# Validate the picking
picking.button_validate()
```

**Why:** When all quantities are fully received/shipped, `button_validate()` processes the transfer directly. For partial transfers, Odoo automatically triggers the `stock.immediate.transfer` wizard (or `stock.backorder.confirmation`). Writing `state = "done"` directly corrupts quants and creates no valuation entries. Always set `quantity` on moves and call `button_validate()`.

---

## Back Orders

### Let Odoo auto-create back orders, never create them manually

**WRONG:**
```python
# Manually creating a back order picking -- duplicates, wrong references
backorder = self.env["stock.picking"].create({
    "partner_id": original_picking.partner_id.id,
    "picking_type_id": original_picking.picking_type_id.id,
    "location_id": original_picking.location_id.id,
    "location_dest_id": original_picking.location_dest_id.id,
    "origin": original_picking.name,
    "backorder_id": original_picking.id,
    "move_ids": [(0, 0, {
        "name": product.name,
        "product_id": product.id,
        "product_uom_qty": remaining_qty,
        "product_uom": product.uom_id.id,
        "location_id": original_picking.location_id.id,
        "location_dest_id": original_picking.location_dest_id.id,
    })],
})
```

**CORRECT:**
```python
# Process partial quantity on original picking
picking.action_confirm()
picking.action_assign()
for move in picking.move_ids:
    move.quantity = partial_qty  # Less than product_uom_qty
# Validate -- Odoo will prompt for back order creation
result = picking.button_validate()
if isinstance(result, dict) and result.get("res_model") == "stock.backorder.confirmation":
    # Confirm back order creation via wizard
    wizard = self.env["stock.backorder.confirmation"].with_context(
        **result.get("context", {})
    ).create({})
    wizard.process()
# Odoo auto-creates the back order with remaining quantities
backorder = self.env["stock.picking"].search([
    ("backorder_id", "=", picking.id),
])
```

**Why:** Manually creating back orders misses Odoo's internal bookkeeping: the `backorder_id` link, move splitting, reservation transfers, sequence numbering, and the origin trail. The `stock.backorder.confirmation` wizard handles all of this correctly. Manual creation often leads to double moves, incorrect reservations, and broken traceability.

---

## Multi-Step Routes

### Configure routes with push/pull rules, never use a single picking for multi-step

**WRONG:**
```python
# Single picking from supplier to stock -- skips quality check / input zone
picking = self.env["stock.picking"].create({
    "picking_type_id": warehouse.in_type_id.id,
    "location_id": supplier_location.id,
    "location_dest_id": warehouse.lot_stock_id.id,  # Direct to stock
    "move_ids": [(0, 0, {
        "name": product.name,
        "product_id": product.id,
        "product_uom_qty": 100.0,
        "product_uom": product.uom_id.id,
        "location_id": supplier_location.id,
        "location_dest_id": warehouse.lot_stock_id.id,
    })],
})
```

**CORRECT:**
```python
# Configure warehouse for multi-step reception (Settings or code)
warehouse.write({"reception_steps": "two_steps"})
# Options: "one_step", "two_steps", "three_steps"

# Now use procurement to create the correct chain of pickings
procurement_group = self.env["procurement.group"]
procurement_group.run(
    [
        procurement_group.Procurement(
            product,
            100.0,
            product.uom_id,
            warehouse.lot_stock_id,
            "PO-001",
            "PO-001",
            self.env.company,
            {
                "warehouse_id": warehouse,
                "company_id": self.env.company,
            },
        ),
    ],
)
# Odoo auto-creates: Receipt (Supplier -> Input) + Internal (Input -> Stock)
```

**Why:** Multi-step routes (2-step receipt, 3-step delivery) rely on push/pull rules that automatically chain pickings through intermediate locations (input, quality check, packing, output). Creating a single direct picking bypasses these steps, skips quality control zones, and breaks warehouse process compliance. Always configure `reception_steps` / `delivery_steps` on the warehouse and let procurement rules generate the picking chain.

---

## Stock Availability

### Use product availability fields, never query quants directly for availability

**WRONG:**
```python
# Direct quant query -- misses reserved quantities, multi-location issues
quants = self.env["stock.quant"].search([
    ("product_id", "=", product.id),
    ("location_id", "=", stock_location.id),
])
available = sum(quants.mapped("quantity"))
```

**CORRECT:**
```python
# Use the product's computed availability fields
product = product.with_context(warehouse=warehouse.id)

on_hand = product.qty_available       # Physical quantity in stock
forecasted = product.virtual_available  # On hand - outgoing + incoming
incoming = product.incoming_qty        # Expected receipts
outgoing = product.outgoing_qty        # Expected deliveries

# For location-specific availability:
product_loc = product.with_context(location=stock_location.id)
on_hand_at_loc = product_loc.qty_available
```

**Why:** Raw quant `quantity` does not account for reserved stock (already assigned to other pickings). `qty_available` returns on-hand quantity, while `virtual_available` factors in pending incoming and outgoing moves to give the forecasted quantity. These fields are computed by Odoo's stock engine and respect reservations, multi-warehouse setups, and company rules. Direct quant queries lead to over-promising stock and double allocations.

---

## Incoming vs. Outgoing: Location Patterns

### Use correct source/destination locations for receipts and deliveries

**WRONG:**
```python
# Receipt with locations swapped -- product moves OUT of stock instead of IN
receipt = self.env["stock.picking"].create({
    "picking_type_id": warehouse.in_type_id.id,
    "location_id": warehouse.lot_stock_id.id,           # Stock as source?
    "location_dest_id": self.env.ref("stock.stock_location_suppliers").id,
    "move_ids": [(0, 0, {
        "name": product.name,
        "product_id": product.id,
        "product_uom_qty": 20.0,
        "product_uom": product.uom_id.id,
        "location_id": warehouse.lot_stock_id.id,
        "location_dest_id": self.env.ref("stock.stock_location_suppliers").id,
    })],
})
```

**CORRECT:**
```python
# Receipt: Supplier -> Stock (or Input for multi-step)
receipt = self.env["stock.picking"].create({
    "picking_type_id": warehouse.in_type_id.id,
    "location_id": self.env.ref("stock.stock_location_suppliers").id,
    "location_dest_id": warehouse.lot_stock_id.id,
    "move_ids": [(0, 0, {
        "name": product.name,
        "product_id": product.id,
        "product_uom_qty": 20.0,
        "product_uom": product.uom_id.id,
        "location_id": self.env.ref("stock.stock_location_suppliers").id,
        "location_dest_id": warehouse.lot_stock_id.id,
    })],
})

# Delivery: Stock -> Customer
delivery = self.env["stock.picking"].create({
    "picking_type_id": warehouse.out_type_id.id,
    "location_id": warehouse.lot_stock_id.id,
    "location_dest_id": self.env.ref("stock.stock_location_customers").id,
    "move_ids": [(0, 0, {
        "name": product.name,
        "product_id": product.id,
        "product_uom_qty": 10.0,
        "product_uom": product.uom_id.id,
        "location_id": warehouse.lot_stock_id.id,
        "location_dest_id": self.env.ref("stock.stock_location_customers").id,
    })],
})
```

**Why:** Swapped locations cause stock to move in the wrong direction -- a receipt that removes stock or a delivery that adds it. The picking type's `default_location_src_id` and `default_location_dest_id` define the expected flow. Receipts always flow from a virtual supplier/production location INTO the warehouse. Deliveries always flow from the warehouse OUT to a virtual customer location.

---

## Scrap Processing

### Use stock.scrap model, never manually move to scrap location

**WRONG:**
```python
# Manual move to scrap location -- no scrap record, breaks reporting
scrap_location = self.env.ref("stock.stock_location_scrapped")
self.env["stock.move"].create({
    "name": "Scrap",
    "product_id": product.id,
    "product_uom_qty": 3.0,
    "product_uom": product.uom_id.id,
    "location_id": warehouse.lot_stock_id.id,
    "location_dest_id": scrap_location.id,
})._action_done()
```

**CORRECT:**
```python
scrap = self.env["stock.scrap"].create({
    "product_id": product.id,
    "scrap_qty": 3.0,
    "product_uom_id": product.uom_id.id,
    "location_id": warehouse.lot_stock_id.id,
    "lot_id": lot.id,  # Optional: if product is tracked
})
scrap.action_validate()
```

**Why:** The `stock.scrap` model handles valuation write-off, lot tracking, picking association (if scrapped from a picking), and scrap reporting. A raw `stock.move` to the scrap location does not appear in scrap reports, does not trigger valuation adjustments for FIFO/AVCO products, and cannot be linked back to the source picking.

---

## Common Mistakes

### Forgetting to set `product_uom` on stock moves

Every `stock.move` requires `product_uom` (the unit of measure). Omitting it causes a `ValidationError` or defaults to the wrong UoM, leading to incorrect quantity conversions.

### Using `product_qty` instead of `product_uom_qty` on stock moves

`product_qty` is a computed field in the product's base UoM. The writeable demand field is `product_uom_qty`. Writing to `product_qty` directly has no effect or raises an error.

### Not calling `action_assign()` before `button_validate()`

Skipping `action_assign()` means no stock is reserved. `button_validate()` on an unassigned picking may trigger the immediate transfer wizard or fail if no quantities are set on move lines.

### Mixing up `quantity` and `product_uom_qty` on stock.move

In Odoo 17+, `quantity` (formerly `quantity_done`) is the actually processed quantity. `product_uom_qty` is the demanded quantity. Setting `product_uom_qty` to the done amount instead of `quantity` leaves the move showing zero processed.

### Searching stock.picking by `name` without company filter

In multi-company environments, picking sequences can overlap. Always include `("company_id", "=", self.env.company.id)` in search domains to avoid cross-company data leaks.

### Not handling the return value of `button_validate()`

`button_validate()` may return a wizard action dict (for `stock.immediate.transfer` or `stock.backorder.confirmation`) instead of `True`. Code that ignores this return value silently skips wizard processing, leaving pickings in an intermediate state.

---

## Changed in 18.0

- `stock.move` field `quantity_done` renamed to `quantity`. Use `quantity` for Odoo 18.0+.
- `stock.move.line` field `qty_done` renamed to `quantity`. Use `quantity` for Odoo 18.0+.
- `stock.immediate.transfer` wizard behavior changed: Odoo 18.0 auto-fills done quantities when calling `button_validate()` on a fully available picking, reducing wizard prompts.

## Changed in 19.0

- No breaking changes to core WMS models in 19.0. `stock.picking`, `stock.move`, `stock.quant`, and `stock.valuation.layer` APIs remain stable from 18.0.
- Warehouse configuration fields (`reception_steps`, `delivery_steps`) and picking type references (`in_type_id`, `out_type_id`, `int_type_id`) are unchanged.
- Use `quantity` (not `quantity_done` / `qty_done`) on `stock.move` and `stock.move.line` as established in 18.0.

---
*Odoo 17.0/18.0/19.0 Inventory & Warehouse -- loaded by inventory/warehouse generation agents*
