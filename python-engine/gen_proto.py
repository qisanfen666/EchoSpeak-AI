"""
一键生成 gRPC Proto 代码
运行: cd python-engine && python gen_proto.py
"""

import subprocess
import sys
import os

PROTO_SRC = "../go-gateway/proto/aiservice.proto"
PROTO_OUT = "./proto"

def main():
    os.makedirs(PROTO_OUT, exist_ok=True)

    # 在 proto 目录创建 __init__.py
    init_file = os.path.join(PROTO_OUT, "__init__.py")
    if not os.path.exists(init_file):
        with open(init_file, "w") as f:
            f.write("")

    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"-I../go-gateway/proto",
        f"--python_out={PROTO_OUT}",
        f"--grpc_python_out={PROTO_OUT}",
        PROTO_SRC,
    ]

    print(f"Generating from: {PROTO_SRC}")
    print(f"Output to: {PROTO_OUT}")
    print(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        # Fix import path in generated grpc file
        grpc_file = os.path.join(PROTO_OUT, "aiservice_pb2_grpc.py")
        with open(grpc_file, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("import aiservice_pb2 as", "from proto import aiservice_pb2 as")
        with open(grpc_file, "w", encoding="utf-8") as f:
            f.write(content)

        print("[OK] Proto generated successfully!")
        print(f"  - {PROTO_OUT}/aiservice_pb2.py")
        print(f"  - {PROTO_OUT}/aiservice_pb2_grpc.py")
    else:
        print(f"[FAIL] {result.stderr}")
        print("\nMake sure grpcio-tools is installed:")
        print("  pip install grpcio-tools")
        sys.exit(1)


if __name__ == "__main__":
    main()
