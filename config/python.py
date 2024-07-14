config_requires = []
dev_requires = []
install_requires = [
    "flask",
    "google-cloud-datastore",
    "google-api-python-client",
    "bcrypt",
]
build_requires = [
    "pymakehelper",
    "pydmt",
    "pylint",
]
test_requires = []
requires = config_requires + install_requires + build_requires + test_requires
