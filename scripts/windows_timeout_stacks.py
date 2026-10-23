"""Capture native timeout evidence without accepting or masking the failure."""
import subprocess

def capture(pid, work, log):
    debugger = work / 'windows-debugger/cdb.exe'
    if not debugger.is_file():
        return
    try:
        with (work / ('timeout-stack-' + str(pid) + '.log')).open('w') as stack:
            subprocess.run([str(debugger), '-pv', '-p', str(pid), '-c', '~* kb; lm; q'],
                           stdout=stack, stderr=stack, timeout=45, check=False)
    except Exception as error:
        log.write('Native timeout stack capture failed: ' + type(error).__name__ + '\n')
        log.flush()
