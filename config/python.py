config_requires = []
dev_requires = []
install_requires = [
    "flask",
    "oauth2client",
    "bcrypt",
    "gunicorn",
    "cryptography",
    "pygooglecloud",
    # google modules
    "google-cloud-core",
    "google-cloud-quotas",
    "google-cloud-datastore",
    "google-cloud-service-usage",
    "google-cloud-resource-manager",
    "google-api-python-client",
    "google-auth",
]
build_requires = [
    "pymakehelper",
    "pydmt",
    "pylint",
]
test_requires = []
requires = config_requires + install_requires + build_requires + test_requires
