"""Catalog of the real-model dataset used by the dataset stats script and
by experiment_b_refinement.py. Single source of truth for which 19 files
live under MODELS_ROOT, their category, and their license/source - kept
here once so the two scripts that need the model list can't drift apart.

None of these files are checked into the repo (third-party/licensed
models, some multi-hundred-MB); MODELS_ROOT points at the local folder
where they were downloaded and organized as <MODELS_ROOT>/<category>/<file>.
Set the ADAPTIVE_REMESHER_MODELS environment variable to use another
folder; the default is a `models` folder at the repository root.
"""

import os

MODELS_ROOT = os.environ.get(
    "ADAPTIVE_REMESHER_MODELS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"),
)

# (category, filename, display name, source/url, license, notes)
CATALOG = [
    ("Characters", "sculpture_man_woman_egypt.glb", "Sculpture of man and woman (Egyptian excavation)",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-001/1b640e4be73d461abd24eee5e1b61301.glb",
     "CC0 1.0", "Objaverse (museum/excavation scan)"),
    ("Characters", "statuette_woman_stargazer.glb", '1993.165 Statuette of a Woman: "The Stargazer"',
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-104/84cd036f133e449ba7e67d33eb8047ee.glb",
     "CC0 1.0", "Objaverse, Cleveland Museum of Art"),
    ("Characters", "porcelain_woman_hunting_clothes.glb", "Statuette of a Woman in Hunting Clothes",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-023/27fa02a655504a7e8160a0b167105daf.glb",
     "CC0 1.0", "Objaverse (18th-c. Meissen porcelain, museum scan)"),
    ("Objects", "barrel.gltf", "Barrel 03", "https://polyhaven.com/a/barrel_03", "CC0 1.0", "Poly Haven"),
    ("Objects", "carrot_cake.gltf", "Carrot Cake", "https://polyhaven.com/a/carrot_cake", "CC0 1.0", "Poly Haven"),
    ("Objects", "wooden_elephant.gltf", "Carved Wooden Elephant", "https://polyhaven.com/a/carved_wooden_elephant", "CC0 1.0", "Poly Haven"),
    ("Mechanical", "bolt_cutters.gltf", "Bolt Cutters 01", "https://polyhaven.com/a/bolt_cutters_01", "CC0 1.0", "Poly Haven"),
    ("Mechanical", "drill.gltf", "Drill 01", "https://polyhaven.com/a/Drill_01", "CC0 1.0", "Poly Haven"),
    ("Organic", "trilobite_fossil.glb", "Trilobite: Agnostid",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-023/2281648114694e4e901632eacf188636.glb",
     "CC0 1.0", "Objaverse (Smithsonian/PRI fossil scan)"),
    ("Organic", "blastoid_fossil.glb", "Blastoid: Pentremites godani (PRI 70771)",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-023/d470399390d84c81ad409c4f33f21a24.glb",
     "CC0 1.0", "Objaverse (PRI fossil scan)"),
    ("Organic", "trex_tooth_fossil.glb", "Vertebrate: T. rex tooth (PRI 55205)",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-030/353154e076364f6db8fd2b053f72be3f.glb",
     "CC0 1.0", "Objaverse (PRI fossil scan)"),
    ("Organic", "lion_scan_schoenbrunn.glb", "Loewe (lion)",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-086/cb90310b9d354315b65a7640f9161339.glb",
     "CC0 1.0", "Objaverse (Schoenbrunn Zoo Vienna scan)"),
    ("Scanned", "google_scanned_shark.obj", "Weisshai Great White Shark",
     "https://fuel.gazebosim.org/1.0/GoogleResearch/models/Weisshai_Great_White_Shark/tip/files/meshes/model.obj",
     "CC-BY 4.0", "Google Scanned Objects"),
    ("Scanned", "google_scanned_vegetable_garden.obj", "Vegetable Garden",
     "https://fuel.gazebosim.org/1.0/GoogleResearch/models/VEGETABLE_GARDEN/tip/files/meshes/model.obj",
     "CC-BY 4.0", "Google Scanned Objects"),
    ("Scanned", "rat_netsuke_scan.glb", "Curled Rat netsuke, test scan",
     "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-023/761a0f3150b042e8904ec63b54abd0f0.glb",
     "CC0 1.0", "Objaverse, 3D Live Scanner Pro phone scan"),
    ("Scanned", "stanford_bunny.ply", "Stanford Bunny (reconstruction)",
     "http://graphics.stanford.edu/pub/3Dscanrep/bunny.tar.gz",
     "Free for research, attribution required", "Stanford 3D Scanning Repository"),
    ("Scanned", "stanford_armadillo.ply", "Stanford Armadillo (reconstruction)",
     "http://graphics.stanford.edu/pub/3Dscanrep/armadillo/Armadillo.ply.gz",
     "Free for research, attribution required", "Stanford 3D Scanning Repository"),
    ("Generated", "meshy_ai_electrocardiograph.obj", "Electrocardiograph Machine (AI-generated)",
     "(generated locally with Meshy AI, not a downloaded URL)",
     "Own generation - no third-party license constraint", "Meshy AI text/image-to-3D"),
    ("Generated", "meshy_ai_heart.obj", "Realistic Heart (AI-generated)",
     "(generated locally with Meshy AI, not a downloaded URL)",
     "Own generation - no third-party license constraint", "Meshy AI text/image-to-3D"),
]

# 5 models spanning size/category/behavior, used by the parameter-sweep
# experiments (D, E) where the sheet templates only have room for a
# "handful of representative models" x several parameter values, not the
# full 19-model dataset: a clean, well-behaved baseline (bunny), a small
# model that needed many refinement iterations in Experiment B (carrot
# cake), a large organic scan (trilobite), the one real-dataset anomaly
# where refinement made forward error worse instead of better (lion, see
# experiment_b_refinement.py's results), and the largest mesh in the
# dataset (electrocardiograph).
REPRESENTATIVE_FILENAMES = {
    "stanford_bunny.ply",
    "carrot_cake.gltf",
    "trilobite_fossil.glb",
    "lion_scan_schoenbrunn.glb",
    "meshy_ai_electrocardiograph.obj",
}
REPRESENTATIVE_MODELS = [
    (cat, fname, display) for cat, fname, display, _url, _lic, _notes in CATALOG
    if fname in REPRESENTATIVE_FILENAMES
]
