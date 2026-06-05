"""
EchoSpeak AI — Python AI Engine 入口
"""

import logging
import asyncio
from concurrent import futures

import grpc

# 动态导入 proto（运行时生成）
try:
    import proto.aiservice_pb2 as pb2
    import proto.aiservice_pb2_grpc as pb2_grpc
    PROTO_AVAILABLE = True
except ImportError:
    PROTO_AVAILABLE = False
    print("[WARN] Proto not generated yet, gRPC server will use placeholder")

from config import config

logging.basicConfig(level=logging.DEBUG if config.DEBUG else logging.INFO)
logger = logging.getLogger(__name__)


class AIServiceServicer(pb2_grpc.AIServiceServicer if PROTO_AVAILABLE else object):
    """AI 服务 gRPC 实现——当前只有 Health，后续逐步添加"""

    def Health(self, request, context):
        """健康检查——验证 Go ↔ Python 通信"""
        logger.info(f"[gRPC] Health check from peer: {context.peer()}")
        if PROTO_AVAILABLE:
            return pb2.HealthResponse(ok=True, message="Python AI Engine is running")
        return None


async def serve():
    """启动 gRPC Server"""
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))

    if PROTO_AVAILABLE:
        pb2_grpc.add_AIServiceServicer_to_server(AIServiceServicer(), server)

    server.add_insecure_port(config.GRPC_LISTEN_ADDR)

    await server.start()
    logger.info(f"[gRPC] Listening on {config.GRPC_LISTEN_ADDR}")
    logger.info("[gRPC] Ready — waiting for Go gateway connections...")

    await server.wait_for_termination()


if __name__ == "__main__":
    if not PROTO_AVAILABLE:
        print("=" * 50)
        print("  Proto 文件未生成！请先运行:")
        print("  cd python-engine")
        print("  python gen_proto.py")
        print("=" * 50)
    asyncio.run(serve())
