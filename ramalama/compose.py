# ramalama/generate/compose.py

import os
import shlex
from typing import Optional, Tuple

from ramalama.common import MNT_DIR, RAG_DIR, genname, get_accel_env_vars
from ramalama.file import PlainFile
from ramalama.version import version


class Compose:
    """
    Generates a docker-compose.yaml file from a ramalama serve configuration,
    structurally mirroring the logic in the Quadlet and Kube classes.
    """

    def __init__(
        self,
        model_name: str,
        model_paths: Tuple[str, str],
        chat_template_paths: Optional[Tuple[str, str]],
        mmproj_paths: Optional[Tuple[str, str]],
        args,
        exec_args,
    ):
        self.src_model_path, self.dest_model_path = model_paths
        self.src_chat_template_path, self.dest_chat_template_path = (
            chat_template_paths if chat_template_paths is not None else ("", "")
        )
        self.src_mmproj_path, self.dest_mmproj_path = mmproj_paths if mmproj_paths is not None else ("", "")
        self.src_model_path = self.src_model_path.removeprefix("oci://")

        self.model_name = model_name
        custom_name = getattr(args, "name", None)
        self.name = custom_name if custom_name else f"ramalama-{model_name}"
        self.args = args
        self.exec_args = exec_args
        self.image = args.image

    def _gen_volumes(self) -> str:
        """
        Generates the complete 'volumes' block for the Compose file.
        """
        volumes = "    volumes:"

        # Model Volume
        volumes += self._gen_model_volume()

        # RAG Volume
        if getattr(self.args, "rag", None):
            volumes += self._gen_rag_volume()

        # Chat Template Volume
        if self.src_chat_template_path and os.path.exists(self.src_chat_template_path):
            volumes += self._gen_chat_template_volume()

        # MMProj Volume
        if self.src_mmproj_path and os.path.exists(self.src_mmproj_path):
            volumes += self._gen_mmproj_volume()

        return volumes

    def _gen_model_volume(self) -> str:
        """Generates the volume mount for the main AI model from a host path."""
        return f'\n      - "{self.src_model_path}:{self.dest_model_path}:ro"'

    def _gen_rag_volume(self) -> str:
        """
        Generates the RAG volume mount. It now supports OCI images directly
        for both Docker Compose and Podman Compose.
        """
        rag_source = self.args.rag
        volume_str = ""

        if rag_source.startswith("oci:"):
            oci_image = rag_source.removeprefix("oci:")
            # This is the standard long-form syntax for image volumes, now supported by Docker.
            volume_str = f"""
      - type: image
        source: {oci_image}
        target: {RAG_DIR}
        image:
          readonly: true"""

        elif os.path.exists(rag_source):
            # Standard host path mount
            volume_str = f'\n      - "{rag_source}:{RAG_DIR}:ro"'

        return volume_str

    def _gen_chat_template_volume(self) -> str:
        """Generates the volume mount for a chat template file."""
        return f'\n      - "{self.src_chat_template_path}:{self.dest_chat_template_path}:ro"'

    def _gen_mmproj_volume(self) -> str:
        """Generates the volume mount for a multimodal projection file."""
        return f'\n      - "{self.src_mmproj_path}:{self.dest_mmproj_path}:ro"'

    def _gen_devices(self) -> str:
        """Generates the 'devices' block for AMD/Intel GPUs."""
        device_list = []
        for dev_path in ["/dev/dri", "/dev/kfd", "/dev/accel"]:
            if os.path.exists(dev_path):
                device_list.append(dev_path)

        if not device_list:
            return ""

        devices_str = "    devices:"
        for dev in device_list:
            devices_str += f'\n      - "{dev}:{dev}"'
        return devices_str

    def _gen_ports(self) -> str:
        """Generates the 'ports' block."""
        port_arg = getattr(self.args, "port", None)
        if not port_arg:
            # Default to 8080 if no port is specified
            return '    ports:\n      - "8080:8080"'

        p = port_arg.split(":", 2)
        host_port = p[1] if len(p) > 1 else p[0]
        container_port = p[0]
        return f'    ports:\n      - "{host_port}:{container_port}"'

    def _gen_environment(self) -> str:
        """Generates the 'environment' block."""
        env_vars = get_accel_env_vars()
        # Allow user to override with --env
        if getattr(self.args, "env", None):
            for e in self.args.env:
                key, val = e.split("=", 1)
                env_vars[key] = val

        if not env_vars:
            return ""

        env_spec = "    environment:"
        for k, v in env_vars.items():
            env_spec += f'\n      - {k}={v}'
        return env_spec

    def _gen_gpu_deployment(self) -> str:
        """
        Generates the 'deploy' block for NVIDIA GPU access, the modern
        standard for Docker Compose.
        """
        # Heuristic: if 'cuda' is in the image name, assume NVIDIA GPU needed.
        if "cuda" not in self.image:
            return ""

        return """\
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]"""

    def _gen_command(self) -> str:
        """Generates the 'command' field from the execution arguments."""
        if not self.exec_args:
            return ""
        # shlex.join is perfect for creating a command string from a list
        cmd = shlex.join(self.exec_args)
        return f"    command: {cmd}"

    def generate(self) -> PlainFile:
        """
        Assembles and returns the full docker-compose.yaml file content.
        """
        _version = version()

        # Generate all the dynamic sections of the YAML file
        volumes_string = self._gen_volumes()
        ports_string = self._gen_ports()
        environment_string = self._gen_environment()
        devices_string = self._gen_devices()
        gpu_deploy_string = self._gen_gpu_deployment()
        command_string = self._gen_command()

        # Assemble the final file content
        content = f"""\
# Save this output to a 'docker-compose.yaml' file and run 'docker compose up'.
#
# Created with ramalama-{_version}

services:
  {self.model_name}:
    container_name: {self.name}
    image: {self.image}
{volumes_string}
{ports_string}
{environment_string}
{devices_string}
{gpu_deploy_string}
{command_string}
    restart: unless-stopped
"""
        # Clean up any empty lines that might result from empty sections
        content = "\n".join(line for line in content.splitlines() if line.strip())

        return genfile(self.name, content)


def genfile(name: str, content: str) -> PlainFile:
    """Creates a PlainFile object for the generated content."""
    file_name = "docker-compose.yaml"
    print(f"Generating Docker Compose file: {file_name}")

    file = PlainFile(file_name)
    file.content = content
    return file
