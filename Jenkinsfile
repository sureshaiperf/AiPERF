pipeline {
    agent any

    environment {
        JAVA_HOME = 'C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.19.10-hotspot'
        JMETER_HOME = 'C:\\jmeter\\apache-jmeter-5.6.3'
        PYTHON = 'C:\\Users\\Suresh.Pittala\\AppData\\Local\\Programs\\Python\\Python312\\python.exe'
        INTELLIGENCE_DIR = 'C:\\practice\\AiPERF\\baselineintelligence'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Clean Workspace') {
            steps {
                bat '''
                if exist logs rmdir /s /q logs
                if exist html rmdir /s /q html
                mkdir logs
                mkdir html
                '''
            }
        }

        stage('Generate Run ID') {
            steps {
                script {
                    env.RUN_ID = "RUN_${env.BUILD_NUMBER}_${new Date().format('yyyyMMdd_HHmmss')}"
                }
                echo "RUN_ID=${env.RUN_ID}"
            }
        }

        stage('JMeter Execution') {
            steps {
                timeout(time: 10, unit: 'MINUTES') {
                    bat '''
                    set "PATH=%JAVA_HOME%\\bin;%PATH%"
                    echo ==== JAVA VERSION ====
                    java -version
                    echo ==== RUNNING JMETER ====
                    call "%JMETER_HOME%\\bin\\jmeter.bat" -n -t API_influx_grafana.jmx -l logs\\results.jtl -e -o html\\report -Jjmeterengine.force.system.exit=true
                    '''
                }
            }
        }

        stage('Collect Execution Data') {
            steps {
                bat '''
                cd /d "%INTELLIGENCE_DIR%"
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
                cd /d "%INTELLIGENCE_DIR%"
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
                cd /d "%INTELLIGENCE_DIR%"
                "%PYTHON%" validate_run.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Run Intelligence Engines') {
            steps {
                bat '''
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
                cd /d "%INTELLIGENCE_DIR%"
                "%PYTHON%" aiperf_findings_package.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Release Gate') {
            steps {
                bat '''
                cd /d "%INTELLIGENCE_DIR%"
                "%PYTHON%" release_gate.py
                if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Generate AI Reports') {
            steps {
                bat '''
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
                archiveArtifacts(
                    artifacts: 'logs/results.jtl,html/report/**',
                    fingerprint: true,
                    allowEmptyArchive: true
                )
            }
        }
    }

}
