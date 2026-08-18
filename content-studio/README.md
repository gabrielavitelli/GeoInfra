# Estúdio de conteúdo matemático + Manim

Os arquivos **não** ficam em `~/content-studio`. Eles estão dentro do repositório **GeoInfra**, na pasta `content-studio/`.

## Como baixar no seu PC

A partir da pasta home (`~`):

```bash
cd ~
git clone -b cursor/content-manim-studio-7ad5 https://github.com/gabrielavitelli/GeoInfra.git
cd GeoInfra/content-studio
chmod +x setup.sh render.sh
./setup.sh
```

Você usa conda (`(base)`). O `setup.sh` cria um `.venv` **dentro** desta pasta — não precisa `conda activate` para renderizar. Use `./render.sh`.

Se você **já tem** o GeoInfra clonado em algum lugar:

```bash
cd /caminho/para/GeoInfra
git fetch origin cursor/content-manim-studio-7ad5
git checkout cursor/content-manim-studio-7ad5
cd content-studio
chmod +x setup.sh render.sh
./setup.sh
```

## Depois do setup

Rode **sempre a partir de** `GeoInfra/content-studio`:

```bash
python3 scripts/conteudo.py              # lista os 8 posts salvos
./render.sh derivada_tangente ql         # render rápido (teste)
./render.sh pitagoras_visual ql          # segunda cena
./render.sh derivada_tangente qh         # alta qualidade
```

Vídeos: `output/videos/`

## Conteúdo salvo

| ID | Arquivo | Cena Manim |
|----|---------|------------|
| 001 | `conteudo/posts/001-derivada-tangente.json` | `derivada_tangente` |
| 002 | `conteudo/posts/002-pitagoras-visual.json` | `pitagoras_visual` |
| 003–008 | `conteudo/posts/` | ainda sem cena |
