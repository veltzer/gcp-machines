-- python deps for this project

dofile("config/shared.lua")

-- append every element of "src" onto "dst"
local function extend(dst, src)
    for _, value in ipairs(src) do
        table.insert(dst, value)
    end
    return dst
end

INSTALL_REQUIRES = {
    "flask",
    "bcrypt",
    "gunicorn",
    "cryptography",
    "pygooglecloud",
    -- google modules
    "google-cloud-core",
    "google-cloud-quotas",
    "google-cloud-datastore",
    "google-cloud-service-usage",
    "google-cloud-resource-manager",
    "google-api-python-client",
    "google-auth",
}
BUILD_REQUIRES = BUILD
TEST_REQUIRES = TEST

REQUIRES = {}
extend(REQUIRES, INSTALL_REQUIRES)
extend(REQUIRES, BUILD_REQUIRES)
extend(REQUIRES, TEST_REQUIRES)
