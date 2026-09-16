pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(
            numToKeepStr: '30',
            artifactNumToKeepStr: '10'
        ))
        timeout(time: 30, unit: 'MINUTES')
    }

    environment {
        JAVA_HOME = 'C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.19.10-hotspot'
        JMETER_HOME = 'C:\\jmeter\\apache-jmeter-5.6.3'

        PYTHON = 'C:\\Users\\Suresh.Pittala\\AppData\\Local\\Programs\\Python\\Python312\\python.exe'
        INTELLIGENCE_DIR = 'C:\\practice\\AiPERF\\baselineintelligence'

        INFLUX_HOST = 'localhost'
        INFLUX_PORT = '8086'
        INFLUX_DB = 'jmeter'

        AIPERF_GATEWAY_URL = 'http://localhost:8090'
        AIPERF_USER_SERVICE_URL = 'http://localhost:8081'
        AIPERF_PRODUCT_SERVICE_URL = 'http://localhost:8082'
        AIPERF_ORDER_SERVICE_URL = 'http://localhost:8083'

        AIPERF_PROTOCOL = 'http'
        AIPERF_HOST = 'localhost'
        AIPERF_PORT = '8090'

        AIPERF_USERS = '10'
        AIPERF_RAMP_SECONDS = '10'
        AIPERF_RAMP_STEPS = '10'
        AIPERF_HOLD_SECONDS = '120'

        AIPERF_HTTP_TIMEOUT_SECONDS = '10'
        AIPERF_MIN_SERVICE_REQUESTS = '1'
        AIPERF_FAIL_ON_MISSING_SERVICE_TRAFFIC = 'true'
        AIPERF_VERIFY_TLS = 'true'
        AIPERF_LOG_LEVEL = 'INFO'

        JMX_FILE = 'API_influx_grafana.jmx'
        JTL_FILE = 'logs\\results.jtl'
        JMETER_REPORT_DIR = 'html\\report'
        AIPERF_REPORT_DIR = 'reports'
    }

    stages {
        stage('Initialize Build') {
            steps {
                script {
                    env.RUN_ID =
                        "RUN_${env.BUILD_NUMBER}_${new Date().format('yyyyMMdd_HHmmss')}"

                    env.RUN_START_EPOCH =
                        System.currentTimeMillis().toString()

                    currentBuild.displayName =
                        "#${env.BUILD_NUMBER} - ${env.RUN_ID}"

                    currentBuild.description =
                        "AiPERF execution: ${env.RUN_ID}"
                }

                echo '============================================'
                echo 'AiPERF Pipeline Initialization'
                echo '============================================'
                echo "RUN_ID          : ${env.RUN_ID}"
                echo "RUN_START_EPOCH : ${env.RUN_START_EPOCH}"
                echo "BUILD_NUMBER    : ${env.BUILD_NUMBER}"
                echo "WORKSPACE       : ${env.WORKSPACE}"
                echo '============================================'
            }
        }

        stage('Clean Workspace Output') {
            steps {
                bat '''
                @echo off

                echo ============================================
                echo Cleaning previous generated output
                echo ============================================

                if exist "logs" (
                    rmdir /s /q "logs"
                )

                if exist "html" (
                    rmdir /s /q "html"
                )

                if exist "reports" (
                    rmdir /s /q "reports"
                )

                mkdir "logs"
                if errorlevel 1 exit /b 1

                mkdir "html"
                if errorlevel 1 exit /b 1

                mkdir "reports"
                if errorlevel 1 exit /b 1

                echo Workspace output directories created.
                '''
            }
        }

        stage('Validate Runtime Dependencies') {
            steps {
                bat '''
                @echo off

                echo ============================================
                echo Validating runtime dependencies
                echo ============================================

                if not exist "%JAVA_HOME%\\bin\\java.exe" (
                    echo ERROR: Java executable not found.
                    echo Expected: %JAVA_HOME%\\bin\\java.exe
                    exit /b 1
                )

                if not exist "%JMETER_HOME%\\bin\\jmeter.bat" (
                    echo ERROR: JMeter executable not found.
                    echo Expected: %JMETER_HOME%\\bin\\jmeter.bat
                    exit /b 1
                )

                if not exist "%PYTHON%" (
                    echo ERROR: Python executable not found.
                    echo Expected: %PYTHON%
                    exit /b 1
                )

                if not exist "%WORKSPACE%\\%JMX_FILE%" (
                    echo ERROR: JMeter test plan not found.
                    echo Expected: %WORKSPACE%\\%JMX_FILE%
                    exit /b 1
                )

                if not exist "%INTELLIGENCE_DIR%\\actuator_metrics_collector.py" (
                    echo ERROR: actuator_metrics_collector.py not found.
                    exit /b 1
                )

                if not exist "%INTELLIGENCE_DIR%\\transaction_service_mapping.json" (
                    echo ERROR: transaction_service_mapping.json not found.
                    exit /b 1
                )

                set "PATH=%JAVA_HOME%\\bin;%PATH%"

                echo.
                echo ==== JAVA VERSION ====
                java -version
                if errorlevel 1 exit /b 1

                echo.
                echo ==== JMETER VERSION ====
                call "%JMETER_HOME%\\bin\\jmeter.bat" -v
                if errorlevel 1 exit /b 1

                echo.
                echo ==== PYTHON VERSION ====
                "%PYTHON%" --version
                if errorlevel 1 exit /b 1

                echo.
                echo ==== PYTHON SCRIPT VALIDATION ====
                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\actuator_metrics_collector.py"
                if errorlevel 1 exit /b 1

                echo.
                echo ==== MAPPING JSON VALIDATION ====
                "%PYTHON%" -c "import json; json.load(open(r'%INTELLIGENCE_DIR%\\transaction_service_mapping.json', encoding='utf-8')); print('Transaction-service mapping JSON is valid')"
                if errorlevel 1 exit /b 1

                echo Runtime dependency validation completed.
                '''
            }
        }

        stage('Validate Microservices') {
            steps {
                bat '''
                @echo off

                echo ============================================
                echo Validating AiPERF microservices
                echo ============================================

                powershell -NoProfile -ExecutionPolicy Bypass -Command ^
                  "$ErrorActionPreference = 'Stop';" ^
                  "$services = @(" ^
                  "  @{Name='Gateway'; Url='%AIPERF_GATEWAY_URL%/actuator/health'}," ^
                  "  @{Name='User Service'; Url='%AIPERF_USER_SERVICE_URL%/actuator/health'}," ^
                  "  @{Name='Product Service'; Url='%AIPERF_PRODUCT_SERVICE_URL%/actuator/health'}," ^
                  "  @{Name='Order Service'; Url='%AIPERF_ORDER_SERVICE_URL%/actuator/health'}" ^
                  ");" ^
                  "foreach ($service in $services) {" ^
                  "  Write-Host ('Checking ' + $service.Name + ': ' + $service.Url);" ^
                  "  $response = Invoke-RestMethod -Uri $service.Url -Method Get -TimeoutSec 10;" ^
                  "  if ($response.status -ne 'UP') {" ^
                  "    throw ($service.Name + ' health status is not UP');" ^
                  "  }" ^
                  "  Write-Host ($service.Name + ' is UP');" ^
                  "}"

                if errorlevel 1 (
                    echo ERROR: One or more AiPERF services are unavailable.
                    exit /b 1
                )

                echo All AiPERF services are available.
                '''
            }
        }

        stage('Validate Gateway Routes') {
            steps {
                bat '''
                @echo off

                echo ============================================
                echo Validating Gateway workload routes
                echo ============================================

                powershell -NoProfile -ExecutionPolicy Bypass -Command ^
                  "$ErrorActionPreference = 'Stop';" ^
                  "$headers = @{'X-AiPERF-Run-ID'='%RUN_ID%'; 'Accept'='application/json'};" ^
                  "$routes = @(" ^
                  "  '%AIPERF_GATEWAY_URL%/users'," ^
                  "  '%AIPERF_GATEWAY_URL%/users/1'," ^
                  "  '%AIPERF_GATEWAY_URL%/products'," ^
                  "  '%AIPERF_GATEWAY_URL%/products/1'," ^
                  "  '%AIPERF_GATEWAY_URL%/orders'," ^
                  "  '%AIPERF_GATEWAY_URL%/orders/1'" ^
                  ");" ^
                  "foreach ($route in $routes) {" ^
                  "  Write-Host ('Checking route: ' + $route);" ^
                  "  $response = Invoke-WebRequest -Uri $route -Method Get -Headers $headers -TimeoutSec 10 -UseBasicParsing;" ^
                  "  if ($response.StatusCode -ne 200) {" ^
                  "    throw ('Route returned HTTP ' + $response.StatusCode + ': ' + $route);" ^
                  "  }" ^
                  "  Write-Host ('HTTP 200: ' + $route);" ^
                  "}"

                if errorlevel 1 (
                    echo ERROR: Gateway route validation failed.
                    exit /b 1
                )

                echo All Gateway routes returned HTTP 200.
                '''
            }
        }

        stage('Capture Service Baseline') {
            steps {
                bat '''
                @echo off

                echo ============================================
                echo Capturing before-execution service metrics
                echo RUN_ID: %RUN_ID%
                echo ============================================

                cd /d "%INTELLIGENCE_DIR%"
                if errorlevel 1 exit /b 1

                set "AIPERF_SERVICE_PHASE=before"
                set "AIPERF_FAIL_ON_MISSING_SERVICE_TRAFFIC=false"

                "%PYTHON%" actuator_metrics_collector.py
                if errorlevel 1 (
                    echo ERROR: Service baseline collection failed.
                    exit /b 1
                )

                echo Service baseline collection completed.
                '''
            }
        }

        stage('JMeter Execution') {
    steps {
        timeout(time: 10, unit: 'MINUTES') {
            bat '''
            @echo off

            set "PATH=%JAVA_HOME%\\bin;%PATH%"

            echo ============================================
            echo Running AiPERF JMeter workload
            echo ============================================
            echo RUN_ID       : %RUN_ID%
            echo JMX_FILE     : %WORKSPACE%\\%JMX_FILE%
            echo USERS        : %AIPERF_USERS%
            echo RAMP_SECONDS : %AIPERF_RAMP_SECONDS%
            echo RAMP_STEPS   : %AIPERF_RAMP_STEPS%
            echo HOLD_SECONDS : %AIPERF_HOLD_SECONDS%
            echo TARGET       : %AIPERF_PROTOCOL%://%AIPERF_HOST%:%AIPERF_PORT%
            echo ============================================

            cd /d "%WORKSPACE%"
            if errorlevel 1 exit /b 1

            call "%JMETER_HOME%\\bin\\jmeter.bat" ^
              -n ^
              -t "%WORKSPACE%\\%JMX_FILE%" ^
              -l "%WORKSPACE%\\%JTL_FILE%" ^
              -e ^
              -o "%WORKSPACE%\\%JMETER_REPORT_DIR%" ^
              -JAIPERF_PROTOCOL=%AIPERF_PROTOCOL% ^
              -JAIPERF_HOST=%AIPERF_HOST% ^
              -JAIPERF_PORT=%AIPERF_PORT% ^
              -JAIPERF_USERS=%AIPERF_USERS% ^
              -JAIPERF_RAMP_SECONDS=%AIPERF_RAMP_SECONDS% ^
              -JAIPERF_RAMP_STEPS=%AIPERF_RAMP_STEPS% ^
              -JAIPERF_HOLD_SECONDS=%AIPERF_HOLD_SECONDS% ^
              -JRUN_ID=%RUN_ID% ^
              -Jjmeterengine.force.system.exit=true

            if errorlevel 1 (
                echo ERROR: JMeter execution failed.
                exit /b 1
            )

            if not exist "%WORKSPACE%\\%JTL_FILE%" (
                echo ERROR: JMeter result file was not generated.
                exit /b 1
            )

            if not exist "%WORKSPACE%\\%JMETER_REPORT_DIR%\\index.html" (
                echo ERROR: JMeter HTML report was not generated.
                exit /b 1
            )

            echo JMeter execution completed successfully.
            '''
        }
    }
}

        stage('Capture Run End') {
            steps {
                script {
                    env.RUN_END_EPOCH =
                        System.currentTimeMillis().toString()

   
