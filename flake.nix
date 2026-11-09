{
  description = "ha-grundfos-ble development environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-darwin" ];
    in {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            # uv provisions the interpreter (Python 3.14, see UV_PYTHON) and the
            # virtualenv from pyproject.toml; ruff, mypy and pytest come from the
            # dev dependency group so versions match CI exactly.
            packages = [ pkgs.uv ];

            UV_PYTHON = "3.14";

            shellHook = ''
              uv sync --quiet --group dev
              source .venv/bin/activate
              echo "ha-grundfos-ble dev shell: $(python --version), uv $(uv --version | cut -d' ' -f2)"
            '';
          };
        });
    };
}
