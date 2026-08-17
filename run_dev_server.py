#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发用：在 5055 端口启动 QRecover Web 服务（避开 5000，便于并行调试）"""
import qrecover

if __name__ == '__main__':
    qrecover.app.run(host='127.0.0.1', port=5055, debug=False, use_reloader=False)
