"""Scan every corpus board for block (sheet) structure + anchor effect. KiCad python.
  python block_scan.py <board1.kicad_pcb> [<board2> ...]
For each board: #distinct non-empty blocks, whether floorplan applies (>=2),
the anchor block, and whether the anchor changes the block order.
"""
import sys
sys.path.insert(0, "plugin/plugins")
from autoplace import kicad_io, floorplan, blocks as blocks_mod  # noqa: E402

for src in sys.argv[1:]:
    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        model, _ = kicad_io.load_board(src)
        blocks_mod.detect_blocks(model)  # real pipeline: populates block from sheet
    except Exception as e:  # noqa: BLE001
        print(f"{name:28s} LOAD-ERR {type(e).__name__}: {e}")
        continue
    blocks = sorted({c.block for c in model.components.values() if c.block})
    members = floorplan._members(model)
    applies = len(members) >= 2
    if applies:
        adj = floorplan._block_adj(model)
        anchor = floorplan._anchor_block(model, members)
        o_wo = floorplan._order_chain(members, adj)
        o_w = floorplan._order_chain(members, adj, anchor)
        changed = o_w != o_wo
        print(f"{name:28s} blocks={len(blocks):2d} APPLIES anchor={anchor!r} "
              f"changed={changed}  wo={o_wo} w={o_w}")
    else:
        print(f"{name:28s} blocks={len(blocks):2d} floorplan-inert (needs >=2)")
