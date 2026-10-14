project = "arrowmodel"
copyright = "2026, Anentropic"
author = "Anentropic"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
    "autoapi.extension",
]

# Sphinx AutoAPI
autoapi_dirs = ["../src/arrowmodel"]
autoapi_root = "reference/api"
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
]

# Theme
html_theme = "shibuya"
html_theme_options = {
    "github_url": "https://github.com/s-layer/arrowmodel",
}

# Intersphinx
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
    "pyarrow": ("https://arrow.apache.org/docs/", None),
}

# Exclude patterns
exclude_patterns = ["_build", "src", "scripts"]
