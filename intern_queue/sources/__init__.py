from intern_queue.sources import simplify, speedyapply, vansh

# priority order — earlier sources win when the same job appears in several
ALL_SOURCES = [
    (simplify.NAME, simplify.fetch),
    (vansh.NAME, vansh.fetch),
    ("speedyapply_swe", speedyapply.make_fetch("speedyapply_swe")),
    ("speedyapply_ai", speedyapply.make_fetch("speedyapply_ai")),
]
