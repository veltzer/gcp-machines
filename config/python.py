config_requires = []
dev_requires = []
install_requires = [
    "flask",
    "google-cloud-datastore",
    "google-api-python-client",
    "google-auth",
    "oauth2client",
    "bcrypt",
    "gunicorn",
    "cryptography",
    "pygooglecloud",
]
build_requires = [
    "pymakehelper",
    "pydmt",
    "pylint",
]
test_requires = []
requires = config_requires + install_requires + build_requires + test_requires
