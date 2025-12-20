from appium import webdriver

caps = {
    "platformName": "Android",
    "automationName": "UiAutomator2",
    "deviceName": "Android",
    "appPackage": "com.meyo",
    "appActivity": ".MainActivity",
    "noReset": True
}

driver = webdriver.Remote("http://localhost:4723/wd/hub", caps)
