module.exports = {
  apps: [
    {
      name: 'omni-assistant',
      script: 'main.py',
      cwd: process.env.OMNI_APP_DIR || process.cwd(),
      interpreter: process.env.PYTHON_BIN_PATH || 'python3',
      autorestart: true,
      restart_delay: 5000,
      max_memory_restart: process.env.OMNI_MEMORY_LIMIT || '300M',
      kill_timeout: 10000,
      time: true,
      env: { PYTHONUNBUFFERED: '1' },
    },
  ],
};
