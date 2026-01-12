{
  description = "Nix devshells for XiangShan-utils";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
  };

  outputs = {nixpkgs, ...}: let
    system = "x86_64-linux";
    pkgs = import nixpkgs { inherit system; };
  in {
    devShells.${system}.default = pkgs.mkShell {
      packages = with pkgs; [
        python3
      ];
      shellHook = ''
        export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
      '';
    };
  };
}
