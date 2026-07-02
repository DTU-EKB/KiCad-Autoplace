"""Does the flow anchor change the block order on a board? (placement-level, KiCad python)
  python anchor_diag.py <board.kicad_pcb>
Prints sheet count, the anchor block, order WITHOUT anchor, order WITH anchor, and
whether they differ. If identical -> the FreeRouting route is guaranteed neutral.
"""
import sys
sys.path.insert(0, "plugin/plugins")

from autoplace import kicad_io, floorplan, blocks  # noqa: E402

src = sys.argv[1]
model, _ = kicad_io.load_board(src)
blocks.detect_blocks(model)  # real pipeline step: populates Component.block from sheet

members = floorplan._members(model)
adj = floorplan._block_adj(model)
anchor = floorplan._anchor_block(model, members)

order_without = floorplan._order_chain(members, adj)               # largest-block start
order_with = floorplan._order_chain(members, adj, anchor)          # anchored start

blocks = sorted({c.block for c in model.components.values() if c.block})
conns = [(r, c.block, [p.net for p in c.pads])
         for r, c in model.components.items() if c.is_connector]

print(f"board={src.rsplit('/',1)[-1]}")
print(f"distinct_blocks({len(blocks)})={blocks}")
print(f"floorplan_applies(>=2 blocks)={len(members) >= 2}")
print(f"n_connectors={len(conns)}")
print(f"connectors(ref,block,nets)={conns[:12]}")
print(f"anchor_block={anchor}")
print(f"order_WITHOUT_anchor={order_without}")
print(f"order_WITH_anchor   ={order_with}")
print("RESULT:", "ORDER CHANGED (route to measure)" if order_with != order_without
      else "ORDER IDENTICAL -> route guaranteed neutral")
