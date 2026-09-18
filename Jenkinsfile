pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        timeout(time: 30, unit: 'MINUTES')
    }

    environment {
        JAVA_HOME = 'C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.19.10-hotspot'
        JMETER_HOME = 'C:\\jmeter\\apache-jmeter-5.6.3'
        PYTHON = 'C:\\Users\\Suresh.Pittala\\AppData\\Local\\Programs\\Python\\Python312\\python.exe'
        INTELLIGENCE_DIR = 'C:\\practice\\AiPERF\\baselineintelligence'

        INFLUX_HOST = 'localhost'
        INFLUX_PORT = '8086'
        INFLUX_DATABASE = 'jmeter'
        INFLUX_DB = 'jmeter'

        AIPERF_BASELINE_ID = 'AIPERF_LOCAL_API_V1'
        AIPERF_APPROVED_BASELINE_RUN_ID = 'RUN_286_20260918_014154'

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
        AIPERF_VERIFY_TLS = 'true'
        AIPERF_LOG_LEVEL = 'INFO'

        JMX_FILE = 'API_influx_grafana.jmx'
        JTL_FILE = 'logs\\results.jtl'
        JMETER_REPORT_DIR = 'html\\report'
    }

    stages {
        stage('Initialize Build') {
            steps {
                script {
                    env.RUN_ID = "RUN_${env.BUILD_NUMBER}_${new Date().format('yyyyMMdd_HHmmss')}"
                    env.RUN_START_EPOCH = System.currentTimeMillis().toString()
                    currentBuild.displayName = "#${env.BUILD_NUMBER} - ${env.RUN_ID}"
                    currentBuild.description = "AiPERF execution: ${env.RUN_ID}"
                }
                echo "RUN_ID=${env.RUN_ID}, RUN_START_EPOCH=${env.RUN_START_EPOCH}"
            }
        }

        stage('Clean Workspace Output') {
            steps {
                bat '''
                @echo off
                if exist logs rmdir /s /q logs
                if exist html rmdir /s /q html
                if exist reports rmdir /s /q reports
                mkdir logs
                if errorlevel 1 exit /b 1
                mkdir html
                if errorlevel 1 exit /b 1
                mkdir reports
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Validate Runtime Dependencies') {
            steps {
                bat '''
                @echo off
                if not exist "%JAVA_HOME%\\bin\\java.exe" exit /b 1
                if not exist "%JMETER_HOME%\\bin\\jmeter.bat" exit /b 1
                if not exist "%PYTHON%" exit /b 1
                if not exist "%WORKSPACE%\\%JMX_FILE%" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\actuator_metrics_collector.py" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\transaction_service_mapping.json" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\baseline_selection.py" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\baseline_registry.py" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\transaction_comparison_report.py" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\transaction_comparison_matrix.py" exit /b 1
                if not exist "%INTELLIGENCE_DIR%\\service_comparison_writer.py" exit /b 1

                set "PATH=%JAVA_HOME%\\bin;%PATH%"
                java -version
                if errorlevel 1 exit /b 1

                call "%JMETER_HOME%\\bin\\jmeter.bat" -v
                if errorlevel 1 exit /b 1

                "%PYTHON%" --version
                if errorlevel 1 exit /b 1

                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\actuator_metrics_collector.py"
                if errorlevel 1 exit /b 1
                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\baseline_selection.py"
                if errorlevel 1 exit /b 1
                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\baseline_registry.py"
                if errorlevel 1 exit /b 1
                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\transaction_comparison_report.py"
                if errorlevel 1 exit /b 1
                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\transaction_comparison_matrix.py"
                if errorlevel 1 exit /b 1
                "%PYTHON%" -m py_compile "%INTELLIGENCE_DIR%\\service_comparison_writer.py"
                if errorlevel 1 exit /b 1

                "%PYTHON%" -c "import json; json.load(open(r'%INTELLIGENCE_DIR%\\transaction_service_mapping.json', encoding='utf-8')); print('Mapping JSON valid')"
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Validate Approved Baseline') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"
                if errorlevel 1 exit /b 1

                "%PYTHON%" -c "import os; from influxdb import InfluxDBClient; from baseline_selection import latest_approved_registry, latest_execution; baseline_id=os.environ['AIPERF_BASELINE_ID']; run_id=os.environ['AIPERF_APPROVED_BASELINE_RUN_ID']; client=InfluxDBClient(host=os.environ.get('INFLUX_HOST','localhost'),port=int(os.environ.get('INFLUX_PORT','8086')),username=os.environ.get('INFLUX_USER') or None,password=os.environ.get('INFLUX_PASSWORD') or None,database=os.environ.get('INFLUX_DATABASE',os.environ.get('INFLUX_DB','jmeter')),timeout=int(os.environ.get('INFLUX_TIMEOUT_SECONDS','30'))); client.ping(); registry=latest_approved_registry(client,baseline_id,run_id); execution=latest_execution(client,run_id); assert registry, 'Approved baseline registry unavailable'; assert str(registry.get('status','')).upper() == 'APPROVED', 'Baseline registry status is not APPROVED'; assert str(registry.get('representative_run_id','')) == run_id, 'Baseline registry run mismatch'; assert execution, 'Approved baseline execution history unavailable'; print('APPROVED BASELINE VALIDATED:',baseline_id,run_id); client.close()"
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Validate Microservices and Routes') {
            steps {
                bat '''
                @echo off
                powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $urls=@('%AIPERF_GATEWAY_URL%/actuator/health','%AIPERF_USER_SERVICE_URL%/actuator/health','%AIPERF_PRODUCT_SERVICE_URL%/actuator/health','%AIPERF_ORDER_SERVICE_URL%/actuator/health'); foreach($url in $urls){$r=Invoke-RestMethod -Uri $url -TimeoutSec 10; if($r.status -ne 'UP'){throw ('Service not UP: '+$url)}; Write-Host ('UP: '+$url)}"
                if errorlevel 1 exit /b 1

                powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $h=@{'X-AiPERF-Run-ID'='%RUN_ID%';'Accept'='application/json'}; $urls=@('%AIPERF_GATEWAY_URL%/users','%AIPERF_GATEWAY_URL%/users/1','%AIPERF_GATEWAY_URL%/products','%AIPERF_GATEWAY_URL%/products/1','%AIPERF_GATEWAY_URL%/orders','%AIPERF_GATEWAY_URL%/orders/1'); foreach($url in $urls){$r=Invoke-WebRequest -Uri $url -Headers $h -TimeoutSec 10 -UseBasicParsing; if($r.StatusCode -ne 200){throw ('Route failed: '+$url)}; Write-Host ('HTTP 200: '+$url)}"
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Capture Service Baseline') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"
                if errorlevel 1 exit /b 1

                set "AIPERF_SERVICE_PHASE=before"
                set "AIPERF_FAIL_ON_MISSING_SERVICE_TRAFFIC=false"

                "%PYTHON%" actuator_metrics_collector.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('JMeter Execution') {
            steps {
                timeout(time: 10, unit: 'MINUTES') {
                    bat '''
                    @echo off
                    set "PATH=%JAVA_HOME%\\bin;%PATH%"
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
                    if errorlevel 1 exit /b 1

                    if not exist "%WORKSPACE%\\%JTL_FILE%" exit /b 1
                    if not exist "%WORKSPACE%\\%JMETER_REPORT_DIR%\\index.html" exit /b 1
                    '''
                }
            }
        }

        stage('Capture Run End') {
            steps {
                script {
                    env.RUN_END_EPOCH = System.currentTimeMillis().toString()
                    env.RUN_DURATION_MS = (env.RUN_END_EPOCH.toLong() - env.RUN_START_EPOCH.toLong()).toString()
                }
                echo "RUN_END_EPOCH=${env.RUN_END_EPOCH}, RUN_DURATION_MS=${env.RUN_DURATION_MS}"
            }
        }

        stage('Collect Execution Data') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"
                if errorlevel 1 exit /b 1

                set "AIPERF_SERVICE_PHASE=after"
                set "AIPERF_FAIL_ON_MISSING_SERVICE_TRAFFIC=false"
                set "JTL_PATH=%WORKSPACE%\\%JTL_FILE%"

                "%PYTHON%" actuator_metrics_collector.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" transaction_history_writer.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" execution_history_writer.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" service_health_intelligence.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Build Comparisons') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"
                if errorlevel 1 exit /b 1

                "%PYTHON%" baseline_compare.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" transaction_comparison_report.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" service_comparison_writer.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" transaction_comparison_matrix.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" ai_variance_ranking.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" trend_analysis.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" forecast.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Validate Run Data') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"
                "%PYTHON%" validate_run.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Generate Comparison Report') {
            steps {
                bat '''
                @echo off
                set "AIPERF_REPORT_DIR=%WORKSPACE%\\reports"
                "%PYTHON%" "%INTELLIGENCE_DIR%\\aiperf_comparison_report.py"
                if errorlevel 1 exit /b 1
                if not exist "%WORKSPACE%\\reports\\index.html" exit /b 1
                '''
            }
        }

        stage('Run Intelligence Engines') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"

                "%PYTHON%" similar_execution.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" readiness_score.py --run-id "%RUN_ID%"
                if errorlevel 1 exit /b 1

                "%PYTHON%" anomaly_detection.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" ai_rca_engine.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Build Findings and Knowledge Layer') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"
                "%PYTHON%" aiperf_findings_package.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Release Gate') {
            steps {
                catchError(
                    buildResult: 'UNSTABLE',
                    stageResult: 'FAILURE',
                    message: 'AiPERF release gate returned a non-proceed decision'
                ) {
                    bat '''
                    @echo off
                    cd /d "%INTELLIGENCE_DIR%"
                    "%PYTHON%" release_gate.py
                    if errorlevel 1 exit /b 1
                    '''
                }
            }
        }

        stage('Generate AI Reports') {
            steps {
                bat '''
                @echo off
                cd /d "%INTELLIGENCE_DIR%"

                "%PYTHON%" ai_release_advisor.py
                if errorlevel 1 exit /b 1

                "%PYTHON%" ai_executive_summary.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Publish Reports') {
            steps {
                perfReport sourceDataFiles: 'logs/results.jtl'

                publishHTML(target: [
                    reportDir: 'html/report',
                    reportFiles: 'index.html',
                    reportName: 'JMeter HTML Report',
                    keepAll: true,
                    alwaysLinkToLastBuild: true,
                    allowMissing: false
                ])

                publishHTML(target: [
                    reportDir: 'reports',
                    reportFiles: 'index.html',
                    reportName: 'AiPERF Comparison Report',
                    keepAll: true,
                    alwaysLinkToLastBuild: true,
                    allowMissing: true
                ])

                archiveArtifacts(
                    artifacts: 'logs/results.jtl,html/report/**,reports/**,API_influx_grafana.jmx',
                    fingerprint: true,
                    allowEmptyArchive: false
                )
            }
        }
    }

    post {
        always {
            echo "AiPERF final status: ${currentBuild.currentResult}; RUN_ID=${env.RUN_ID ?: 'NOT_GENERATED'}"
            archiveArtifacts(
                artifacts: 'logs/**,html/**,reports/**',
                fingerprint: true,
                allowEmptyArchive: true
            )
        }

        success {
            echo 'AiPERF pipeline completed successfully.'
        }

        unstable {
            echo 'AiPERF pipeline completed with a non-proceed release decision. Review the findings and reports.'
        }

        failure {
            echo 'AiPERF pipeline failed. Review the first failed stage.'
        }

        aborted {
            echo 'AiPERF pipeline was aborted or timed out.'
        }
    }
}
