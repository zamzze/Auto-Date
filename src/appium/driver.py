from appium import webdriver
from appium.options.android import UiAutomator2Options

options = UiAutomator2Options()

options.platform_name = "Android"
options.device_name = "Android Emulator"
options.automation_name = "UiAutomator2"
options.app_package = "com.Meyo.app"
options.app_activity = ".MainActivity"

# CLAVES para dispositivo físico
options.disable_hidden_api_policy = True
options.ignore_hidden_api_policy_error = True

# Opcional pero recomendable
options.no_reset = True
options.new_command_timeout = 300

driver = webdriver.Remote(
    command_executor="http://localhost:4723",
    options=options
)