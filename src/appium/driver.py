from appium import webdriver
from appium.options.android import UiAutomator2Options

options = UiAutomator2Options()

options.platform_name = "Android"
options.device_name = "RG89VKVOAEIVZLOF"
options.automation_name = "UiAutomator2"

# NO lanzar ninguna app
options.no_reset = True
options.new_command_timeout = 300

# EVITAR instalación / resolución
options.skip_server_installation = True
options.skip_device_initialization = True

options.disable_hidden_api_policy = True
options.ignore_hidden_api_policy_error = True

driver = webdriver.Remote(
    command_executor="http://127.0.0.1:4723",
    options=options
)
