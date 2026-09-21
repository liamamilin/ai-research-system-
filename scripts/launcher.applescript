-- AI Research Console launcher (macOS applet)
--
-- Behaviour:
--   * Click            -> if the web service is up, open it in the browser;
--                         otherwise start it, wait until healthy (max 30 min),
--                         then open the browser. The app stays in the Dock.
--   * Click while open -> "reopen" handler opens the browser again (jump to service).
--   * Watchdog         -> every 60s checks /api/health; if the service has been
--                         unresponsive for 30 continuous minutes, the app quits.
--
-- Notes:
--   * The server is started detached (nohup), so quitting the app does NOT
--     stop the service. Clicking the app again re-attaches and opens the browser.
--   * Server output goes to logs/app_launcher.log

property baseUrl : "http://127.0.0.1:8765"
property projectDir : "/Users/milin/2026/AI_research/Base_CodingCLi"
property pythonBin : "/Users/milin/2026/AI_research/.AI_research/bin/python"
property logFile : "/Users/milin/2026/AI_research/Base_CodingCLi/logs/app_launcher.log"
property lastOk : missing value
property timeoutMinutes : 30

on healthOk()
	try
		do shell script "/usr/bin/curl -s -m 2 -o /dev/null " & baseUrl & "/api/health"
		return true
	on error
		return false
	end try
end healthOk

on startServer()
	try
		do shell script "cd " & quoted form of projectDir & " && /usr/bin/nohup " & quoted form of pythonBin & " run_web.py serve >> " & quoted form of logFile & " 2>&1 < /dev/null &"
		return true
	on error errMsg
		display notification "服务启动失败: " & errMsg with title "AI Research Console"
		return false
	end try
end startServer

on openBrowser()
	open location baseUrl & "/"
end openBrowser

on notifyTimeout()
	display notification "服务 30 分钟无响应，应用将关闭" with title "AI Research Console"
end notifyTimeout

on run
	set lastOk to current date
	if not healthOk() then startServer()

	-- Wait for the service to become healthy (startup, max 30 minutes)
	repeat until healthOk()
		if ((current date) - lastOk) > timeoutMinutes * minutes then
			notifyTimeout()
			quit
			return
		end if
		delay 2
	end repeat

	set lastOk to current date
	openBrowser()
end run

on reopen
	-- Dock/Finder click while the app is running: jump to the service
	if not healthOk() then
		startServer()
	end if
	set lastOk to current date
	openBrowser()
end reopen

on idle
	if healthOk() then
		set lastOk to current date
	else if lastOk is not missing value and ((current date) - lastOk) > timeoutMinutes * minutes then
		notifyTimeout()
		quit
	end if
	return 60
end idle
