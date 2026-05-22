import os
import shlex
import shutil
import tempfile
import docker

class SandboxManager:
    IMAGE_NAME = "kokomi-agent-base"
    
    @classmethod
    def get_client(cls):
        from app.storage import load_prefs
        prefs = load_prefs()
        try:
            if prefs.get("docker_connection") == "remote" and prefs.get("docker_remote_url"):
                return docker.DockerClient(base_url=prefs["docker_remote_url"])
            return docker.from_env()
        except Exception as e:
            print(f"❌ Failed to connect to Docker daemon: {e}")
            return None

    @classmethod
    def get_image_name(cls) -> str:
        from app.storage import load_prefs
        return load_prefs().get("docker_image", "kokomi-agent-base")

    @classmethod
    def ensure_base_image(cls) -> bool:
        """Ensure the Docker image exists. If not, build it natively."""
        client = cls.get_client()
        if not client:
            return False
            
        image_name = cls.get_image_name()
        try:
            client.images.get(image_name)
            return True
        except docker.errors.ImageNotFound:
            pass
        except Exception as e:
            print(f"❌ Docker error checking image: {e}")
            return False
            
        print(f"🐳 Building base Docker image '{image_name}' natively...")
        dockerfile_content = """FROM alpine:3.18
RUN apk update && apk add --no-cache \\
    bash \\
    curl \\
    wget \\
    git \\
    jq \\
    ripgrep \\
    python3 \\
    py3-pip \\
    nodejs \\
    npm \\
    libffi-dev \\
    openssl-dev \\
    build-base \\
    openssh-client \\
    sshpass \\
    docker-cli \\
    docker-compose

WORKDIR /workspace
"""
        tmpdir = tempfile.mkdtemp()
        dockerfile_path = os.path.join(tmpdir, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile_content)
            
        try:
            # Natively build the image using the python SDK
            image, logs = client.images.build(
                path=tmpdir,
                tag=image_name,
                rm=True
            )
            print(f"🐳 Base image '{image_name}' built successfully!")
            return True
        except Exception as e:
            print(f"❌ Failed to build Docker image: {e}")
            return False
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @classmethod
    def start_container(cls, run_id: str, sdir: str) -> bool:
        """Start a dedicated sandbox container for the workflow using Python SDK."""
        client = cls.get_client()
        if not client:
            return False
            
        if not cls.ensure_base_image():
            return False
            
        container_name = f"kokomi-sandbox-{run_id}"
        cls.stop_container(run_id)
        
        abs_sdir = os.path.abspath(sdir)
        os.makedirs(abs_sdir, exist_ok=True)
        
        try:
            volumes = {abs_sdir: {"bind": "/workspace", "mode": "rw"}}
            
            # Mount host Docker socket if available to allow Docker commands inside the container
            if os.path.exists("/var/run/docker.sock"):
                volumes["/var/run/docker.sock"] = {"bind": "/var/run/docker.sock", "mode": "rw"}
                
            # Mount host SSH Agent Socket if available
            environment = {}
            ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
            if ssh_auth_sock and os.path.exists(ssh_auth_sock):
                volumes[ssh_auth_sock] = {"bind": "/ssh-agent", "mode": "rw"}
                environment["SSH_AUTH_SOCK"] = "/ssh-agent"
                
            # Start container in detached mode, mount sdir to /workspace, and tail /dev/null
            container = client.containers.run(
                cls.get_image_name(),
                command="tail -f /dev/null",
                name=container_name,
                volumes=volumes,
                environment=environment,
                working_dir="/workspace",
                detach=True,
                remove=False
            )
            print(f"🐳 Started sandbox container: {container_name}")
            return True
        except Exception as e:
            print(f"❌ Failed to start container: {e}")
            return False

    @classmethod
    def stop_container(cls, run_id: str):
        """Stop and remove the dedicated sandbox container."""
        client = cls.get_client()
        if not client:
            return
               
        container_name = f"kokomi-sandbox-{run_id}"
        try:
            container = client.containers.get(container_name)
            container.remove(force=True)
            print(f"🐳 Stopped & removed sandbox container: {container_name}")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            print(f"❌ Error stopping container: {e}")

    @classmethod
    def execute_in_container(cls, run_id: str, command: str, timeout: int = 60) -> tuple[int, str]:
        """Execute a shell command inside the dedicated workflow container using Python SDK exec_run."""
        client = cls.get_client()
        if not client:
            return -1, "Docker daemon not reachable from Python SDK."
            
        container_name = f"kokomi-sandbox-{run_id}"
        try:
            container = client.containers.get(container_name)
        except docker.errors.NotFound:
            from app.workflow import load_workflows
            db = load_workflows()
            wf = db.get(run_id)
            sdir = wf.get("storage_dir") if wf else None
            if sdir and os.path.isdir(sdir):
                if cls.start_container(run_id, sdir):
                    try:
                        container = client.containers.get(container_name)
                    except Exception as e:
                        return -1, f"Failed to retrieve sandbox container after starting: {e}"
                else:
                    return -1, "Failed to start sandbox container dynamically."
            else:
                return -1, "Docker sandbox container not running and workspace directory not found."
        except Exception as e:
            return -1, f"Docker container retrieve error: {e}"
            
        if container.status != "running":
            try:
                container.start()
            except Exception as e:
                return -1, f"Failed to start existing stopped container: {e}"

        import os
        uid = os.getuid()
        gid = os.getgid()
        # Wrap the user command with `timeout` so hanging commands (e.g. SSH) get killed
        safe_command = f"timeout {timeout} bash -c {shlex.quote(command)}\nEXIT_CODE=$?\nchown -R {uid}:{gid} /workspace 2>/dev/null || true\nexit $EXIT_CODE"
        
        try:
            exec_res = container.exec_run(
                cmd=["bash", "-c", safe_command],
                workdir="/workspace"
            )
            exit_code = exec_res.exit_code
            output = exec_res.output.decode("utf-8", errors="ignore")
            if exit_code == 124:
                output = f"Command timed out after {timeout}s.\n" + output
            return exit_code, output
        except Exception as e:
            try:
                exec_res = container.exec_run(
                    cmd=["sh", "-c", safe_command],
                    workdir="/workspace"
                )
                exit_code = exec_res.exit_code
                output = exec_res.output.decode("utf-8", errors="ignore")
                if exit_code == 124:
                    output = f"Command timed out after {timeout}s.\n" + output
                return exit_code, output
            except Exception as e2:
                return -3, f"Sandbox SDK execution error: {e2}"
