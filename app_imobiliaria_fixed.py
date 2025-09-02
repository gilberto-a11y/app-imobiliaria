from __future__ import annotations
import os, sqlite3, requests
from datetime import datetime, date
from typing import Dict, List, Tuple
from PIL import Image
import pandas as pd
import streamlit as st

st.set_page_config(page_title="CRM Imobiliário", layout="wide")

DB_PATH = "imobiliaria.db"
MEDIA_ROOT = "midia"
IMAGEM_EXTS = {".png",".jpg",".jpeg",".webp"}
VIDEO_EXTS = {".mp4",".mov",".m4v",".avi"}

# ================= Helpers BRL =================
def format_brl(value: float|int|None) -> str:
    try:
        s = f"{float(value or 0):,.2f}"
    except Exception:
        return "0,00"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s

def parse_brl(text: str) -> float | None:
    if text is None: return 0.0
    text = str(text).strip()
    if not text: return 0.0
    try:
        clean = text.replace(".", "").replace(",", ".")
        return float(clean)
    except Exception:
        return None

# ================= DB =================
def get_conn():
    os.makedirs(MEDIA_ROOT, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def _ensure_column(conn, table, column, coltype):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

def init_db():
    conn=get_conn(); c=conn.cursor()
    # Vendedores (proprietários)
    c.execute("""CREATE TABLE IF NOT EXISTS vendedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT, email TEXT, telefone TEXT, creci TEXT)""")
    # Endereço do proprietário
    for col in ["rua","numero","complemento","bairro","cidade_estado","cep"]:
        _ensure_column(conn, "vendedores", col, "TEXT")
    # Imóveis
    c.execute("""CREATE TABLE IF NOT EXISTS properties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE, titulo TEXT, tipo TEXT, valor REAL, descricao TEXT,
        quartos INTEGER, banheiros INTEGER, vagas INTEGER, area REAL,
        rua TEXT, numero TEXT, complemento TEXT, bairro TEXT, cidade_estado TEXT, cep TEXT,
        vendedor_id INTEGER, data_cadastro TEXT,
        FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))""")
    # Mídias
    c.execute("""CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        property_id INTEGER, file_path TEXT, media_type TEXT,
        FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE)""")
    # Interessados
    c.execute("""CREATE TABLE IF NOT EXISTS interessados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        property_id INTEGER, nome TEXT, email TEXT, telefone TEXT,
        mensagem TEXT, status TEXT, valor_proposto REAL, data_interesse TEXT,
        FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE)""")
    _ensure_column(conn, "interessados", "valor_proposto", "REAL")
    # Interações
    c.execute("""CREATE TABLE IF NOT EXISTS interacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        interessado_id INTEGER,
        data_evento TEXT,
        tipo_evento TEXT,
        observacao TEXT,
        FOREIGN KEY(interessado_id) REFERENCES interessados(id) ON DELETE CASCADE)""")
    conn.commit(); conn.close()

# ================= Repositórios =================
def inserir_vendedor(nome,email,telefone,creci, rua=None, numero=None, complemento=None, bairro=None, cidade_estado=None, cep=None)->int:
    conn=get_conn(); c=conn.cursor()
    c.execute("""INSERT INTO vendedores (nome,email,telefone,creci,rua,numero,complemento,bairro,cidade_estado,cep)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (nome,email,telefone,creci,rua,numero,complemento,bairro,cidade_estado,cep))
    vid=c.lastrowid
    conn.commit(); conn.close()
    return vid

def listar_vendedores()->List[Dict]:
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT id,nome,email,telefone,creci,rua,numero,complemento,bairro,cidade_estado,cep FROM vendedores ORDER BY nome")
    rows=c.fetchall(); conn.close()
    return [{"id":r[0],"nome":r[1],"email":r[2],"telefone":r[3],"creci":r[4],
             "rua":r[5],"numero":r[6],"complemento":r[7],"bairro":r[8],"cidade_estado":r[9],"cep":r[10]} for r in rows]

def inserir_imovel(d:Dict)->Tuple[int,str]:
    conn=get_conn(); c=conn.cursor()
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    campos=("titulo","tipo","valor","descricao","quartos","banheiros","vagas","area",
            "rua","numero","complemento","bairro","cidade_estado","cep","vendedor_id")
    c.execute(f"INSERT INTO properties ({','.join(campos)},data_cadastro) VALUES ({','.join(['?']*len(campos))},?)",
              tuple(d.get(k) for k in campos)+(now,))
    pid=c.lastrowid; cod=f"IMO-{pid:04d}"
    c.execute("UPDATE properties SET codigo=? WHERE id=?",(cod,pid))
    conn.commit(); conn.close(); return pid,cod

def listar_imoveis(filtros:Dict|None=None)->List[Dict]:
    conn=get_conn(); c=conn.cursor()
    base=("SELECT p.id,p.codigo,p.titulo,p.tipo,p.valor,p.descricao,p.quartos,p.banheiros,p.vagas,p.area,"
          "p.rua,p.numero,p.complemento,p.bairro,p.cidade_estado,p.cep,p.data_cadastro,p.vendedor_id,"
          "IFNULL(v.nome,'') vendedor_nome FROM properties p LEFT JOIN vendedores v ON v.id=p.vendedor_id")
    where=[]; params=[]
    if filtros:
        if filtros.get("tipo") and filtros["tipo"]!="Todos": where.append("p.tipo=?"); params.append(filtros["tipo"])
        if filtros.get("min_valor") not in (None,0): where.append("p.valor>=?"); params.append(filtros["min_valor"])
        if filtros.get("max_valor") not in (None,0): where.append("p.valor<=?"); params.append(filtros["max_valor"])
        if filtros.get("quartos") not in (None,0): where.append("p.quartos>=?"); params.append(filtros["quartos"])
        if filtros.get("bairro"): where.append("p.bairro LIKE ?"); params.append(f"%{filtros['bairro']}%")
        if filtros.get("cidade_estado"): where.append("p.cidade_estado LIKE ?"); params.append(f"%{filtros['cidade_estado']}%")
        if filtros.get("codigo"): where.append("p.codigo LIKE ?"); params.append(f"%{filtros['codigo']}%")
        if filtros.get("vendedor_id"): where.append("p.vendedor_id=?"); params.append(filtros["vendedor_id"])
    if where: base += " WHERE " + " AND ".join(where)
    base += " ORDER BY datetime(p.data_cadastro) DESC"
    c.execute(base,tuple(params))
    cols=[x[0] for x in c.description]; rows=c.fetchall(); conn.close()
    return [dict(zip(cols,r)) for r in rows]

def inserir_midia(pid,fp,tipo):
    conn=get_conn(); c=conn.cursor()
    c.execute("INSERT INTO media (property_id,file_path,media_type) VALUES (?,?,?)",(pid,fp,tipo))
    conn.commit(); conn.close()

def carregar_midias(pid)->Tuple[List[str],List[str]]:
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT file_path,media_type FROM media WHERE property_id=? ORDER BY id",(pid,))
    rows=c.fetchall(); conn.close()
    return [p for p,t in rows if t=='imagem'], [p for p,t in rows if t=='video']

def inserir_interessado(pid,nome,email,telefone,mensagem,status,valor_proposto:float|None):
    conn=get_conn(); c=conn.cursor()
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""INSERT INTO interessados (property_id,nome,email,telefone,mensagem,status,valor_proposto,data_interesse)
                 VALUES (?,?,?,?,?,?,?,?)""",(pid,nome,email,telefone,mensagem,status,valor_proposto,now))
    conn.commit(); conn.close()

def listar_interessados(pid:int|None=None)->List[Dict]:
    conn=get_conn(); c=conn.cursor()
    if pid:
        c.execute("""SELECT id,property_id,nome,email,telefone,mensagem,status,valor_proposto,data_interesse
                     FROM interessados WHERE property_id=? ORDER BY datetime(data_interesse) DESC""",(pid,))
    else:
        c.execute("""SELECT id,property_id,nome,email,telefone,mensagem,status,valor_proposto,data_interesse
                     FROM interessados ORDER BY datetime(data_interesse) DESC""")
    cols=[x[0] for x in c.description]; rows=c.fetchall(); conn.close()
    return [dict(zip(cols,r)) for r in rows]

def inserir_interacao(interessado_id:int, data_evento:date, tipo_evento:str, observacao:str):
    conn=get_conn(); c=conn.cursor()
    c.execute("""INSERT INTO interacoes (interessado_id, data_evento, tipo_evento, observacao)
                 VALUES (?,?,?,?)""",(interessado_id, data_evento.strftime("%Y-%m-%d"), tipo_evento, observacao))
    conn.commit(); conn.close()

def listar_interacoes(interessado_id:int)->List[Dict]:
    conn=get_conn(); c=conn.cursor()
    c.execute("""SELECT id, interessado_id, data_evento, tipo_evento, observacao
                 FROM interacoes WHERE interessado_id=? ORDER BY date(data_evento) DESC, id DESC""",(interessado_id,))
    cols=[x[0] for x in c.description]; rows=c.fetchall(); conn.close()
    return [dict(zip(cols,r)) for r in rows]

# ================= Utils/CEP/Carousel =================
def sanitize_filename(name): return "".join(c for c in name if c.isalnum() or c in ("-","_",".") )
def save_uploaded_files(pid,files):
    if not files: return
    folder=os.path.join(MEDIA_ROOT,f"{pid:04d}"); os.makedirs(folder,exist_ok=True)
    for up in files:
        name=sanitize_filename(up.name); ext=os.path.splitext(name)[1].lower()
        dest=os.path.join(folder,name)
        with open(dest,"wb") as f: f.write(up.getbuffer())
        tipo="imagem" if ext in IMAGEM_EXTS else "video" if ext in VIDEO_EXTS else None
        if tipo: inserir_midia(pid,dest,tipo)

def busca_cep(cep:str)->Dict|None:
    cep=cep.strip().replace("-","")
    if len(cep)!=8 or not cep.isdigit(): return None
    try:
        r=requests.get(f"https://viacep.com.br/ws/{cep}/json/",timeout=10)
        if r.status_code==200:
            d=r.json()
            if d.get("erro"): return None
            return {"rua":d.get("logradouro",""),
                    "bairro":d.get("bairro",""),
                    "cidade_estado":f"{d.get('localidade','')} / {d.get('uf','')}",
                    "cep":d.get("cep","")}
    except Exception:
        return None
    return None

def _set_if_absent(k,val):
    if k not in st.session_state:
        st.session_state[k]=val

def _advance_index(k,total,step):
    if total>0: st.session_state[k]=(st.session_state.get(k,0)+step)%total

def show_media_carousel(pid):
    imgs,vids=carregar_midias(pid)
    if imgs:
        k=f"img_{pid}"; _set_if_absent(k,0)
        cols=st.columns([1,2,1])
        with cols[0]: st.button("← Anterior",key=f"prev_img{pid}",on_click=_advance_index,args=(k,len(imgs),-1),disabled=len(imgs)<=1,use_container_width=True)
        with cols[1]:
            try: st.image(Image.open(imgs[st.session_state[k]]),use_container_width=True)
            except: st.image(imgs[st.session_state[k]],use_container_width=True)
            st.caption(f"{st.session_state[k]+1} / {len(imgs)}")
        with cols[2]: st.button("Próxima →",key=f"next_img{pid}",on_click=_advance_index,args=(k,len(imgs),1),disabled=len(imgs)<=1,use_container_width=True)
    if vids:
        k=f"vid_{pid}"; _set_if_absent(k,0)
        cols=st.columns([1,2,1])
        with cols[0]: st.button("← Anterior",key=f"prev_vid{pid}",on_click=_advance_index,args=(k,len(vids),-1),disabled=len(vids)<=1,use_container_width=True)
        with cols[1]: st.video(vids[st.session_state[k]]); st.caption(f"{st.session_state[k]+1} / {len(vids)}")
        with cols[2]: st.button("Próxima →",key=f"next_vid{pid}",on_click=_advance_index,args=(k,len(vids),1),disabled=len(vids)<=1,use_container_width=True)

# ================= Páginas =================
def page_cadastrar():
    st.title("Cadastrar Imóvel")
    # Limpeza segura após salvar (executa antes de criar widgets)
    if st.session_state.get("_clear_after_save"):
        for k in [
            "prop_rua","prop_bairro","prop_cidade_estado","prop_cep",
            "prop_numero","prop_complemento",
            "titulo","valor_str","rua","numero","complemento","bairro","cidade_estado","cep",
            "cep_search","cep_search_prop",
            "prop_nome","prop_tel","prop_email","prop_creci",
            "tipo_sel","area_in","quartos_in","banheiros_in","vagas_in","descricao_txt"
        ]:
            st.session_state.pop(k, None)
        st.session_state.pop("_clear_after_save", None)

    proprietarios=listar_vendedores()
    st.markdown("### Proprietário do imóvel")

    opcoes = ["Cadastrar novo","Selecionar existente"]
    idx_default = 0
    modo_prop = st.radio("Como deseja informar o proprietário?", opcoes, horizontal=True, index=idx_default)

    proprietario_id = None
    novo_prop = None
    if modo_prop == "Selecionar existente" and proprietarios:
        termo = st.text_input("Buscar proprietário (nome, telefone ou e-mail)", placeholder="Digite parte do nome, telefone ou e-mail...")
        if termo:
            termo_norm = termo.strip().lower()
            filtrados = [v for v in proprietarios if any(
                termo_norm in str(v.get(c,"")).lower() for c in ["nome","telefone","email"]
            )]
        else:
            filtrados = proprietarios

        if not filtrados:
            st.warning("Nenhum proprietário encontrado para a busca.")
        elif len(filtrados) == 1:
            unico = filtrados[0]
            st.success(f"Selecionado automaticamente: {unico['nome']} ({unico['telefone']})")
            proprietario_id = int(unico["id"])
        else:
            label_sel = st.selectbox(
                "Resultados da busca",
                [f"{v['id']} - {v['nome']} ({v['telefone']})" for v in filtrados]
            )
            proprietario_id = int(label_sel.split(" - ")[0])
    else:
        # ------- CEP DO PROPRIETÁRIO -------
        with st.expander("Preencher endereço do proprietário via CEP", expanded=False):
            cep_search_prop = st.text_input("Digite o CEP do proprietário", key="cep_search_prop", placeholder="00000-000")
            if st.button("Buscar CEP do proprietário", key="btn_busca_cep_prop"):
                info = busca_cep(cep_search_prop)
                if info:
                    st.session_state["prop_rua"] = info.get("rua", "")
                    st.session_state["prop_bairro"] = info.get("bairro", "")
                    st.session_state["prop_cidade_estado"] = info.get("cidade_estado", "")
                    st.session_state["prop_cep"] = info.get("cep", cep_search_prop)
                    st.rerun()
                else:
                    st.warning("CEP inválido ou não encontrado para o proprietário.")

        # Garantir chaves
        _set_if_absent("prop_rua",""); _set_if_absent("prop_bairro",""); _set_if_absent("prop_cidade_estado",""); _set_if_absent("prop_cep","")
        _set_if_absent("prop_numero",""); _set_if_absent("prop_complemento","")

        st.info("Informe os dados do novo proprietário:")
        colp1,colp2=st.columns(2)
        with colp1:
            p_nome = st.text_input("Nome do proprietário", key="prop_nome")
            p_tel  = st.text_input("Telefone do proprietário", key="prop_tel")
            p_rua  = st.text_input("Rua/Av. (proprietário)", key="prop_rua")
            p_num  = st.text_input("Número (proprietário)", key="prop_numero")
            p_comp = st.text_input("Complemento (proprietário)", key="prop_complemento")
        with colp2:
            p_email= st.text_input("Email do proprietário", key="prop_email")
            p_creci= st.text_input("CRECI (opcional)", key="prop_creci")
            p_bairro = st.text_input("Bairro (proprietário)", key="prop_bairro")
            p_cidade_estado = st.text_input("Cidade/Estado (proprietário)", key="prop_cidade_estado")
            p_cep = st.text_input("CEP (proprietário)", key="prop_cep")

        novo_prop = {
            "nome":p_nome,"telefone":p_tel,"email":p_email,"creci":p_creci,
            "rua":p_rua,"numero":p_num,"complemento":p_comp,
            "bairro":p_bairro,"cidade_estado":p_cidade_estado,"cep":p_cep
        }

    # ---------------- Separador ----------------
    st.markdown("## Dados do Imóvel")

    # CEP do imóvel
    with st.expander("Preencher endereço do imóvel via CEP", expanded=False):
        cep_search = st.text_input("Digite o CEP do imóvel", key="cep_search", placeholder="00000-000")
        if st.button("Buscar CEP do imóvel", key="btn_busca_cep"):
            info = busca_cep(cep_search)
            if info:
                st.session_state["rua"] = info.get("rua","")
                st.session_state["bairro"] = info.get("bairro","")
                st.session_state["cidade_estado"] = info.get("cidade_estado","")
                st.session_state["cep"] = info.get("cep", cep_search)
                st.rerun()
            else:
                st.warning("CEP inválido ou não encontrado para o imóvel.")

    # -------- Dados do imóvel --------
    _set_if_absent("rua",""); _set_if_absent("numero",""); _set_if_absent("complemento",""); _set_if_absent("bairro",""); _set_if_absent("cidade_estado",""); _set_if_absent("cep","")
    _set_if_absent("uploader_key", 0)
    with st.form("form_im",clear_on_submit=False):
        c1,c2,c3=st.columns(3)
        with c1:
            titulo=st.text_area("Título do anúncio", height=80, key="titulo")
            tipo=st.selectbox("Tipo",["Compra","Aluguel"], key="tipo_sel")
            valor_str = st.text_input("Valor (R$) — formato 999.999,99", key="valor_str", placeholder="0,00")
            area=st.number_input("Área (m²)",0.0,step=1.0, key="area_in")
        with c2:
            quartos=st.number_input("Quartos",0,step=1, key="quartos_in"); banheiros=st.number_input("Banheiros",0,step=1, key="banheiros_in")
            vagas=st.number_input("Vagas",0,step=1, key="vagas_in")
        with c3:
            rua=st.text_input("Rua/Av (imóvel)", key="rua")
            numero=st.text_input("Número (imóvel)", key="numero")
            complemento=st.text_input("Complemento (imóvel)", key="complemento")
            bairro=st.text_input("Bairro (imóvel)", key="bairro")
            cidade_estado=st.text_input("Cidade/Estado (imóvel)", key="cidade_estado")
            cep=st.text_input("CEP (imóvel)", key="cep")

        st.markdown("<div style='border:1px solid #e6e6e6; border-radius:12px; padding:12px; margin-top:8px;'><b>Descrição do imóvel</b></div>", unsafe_allow_html=True)
        descricao = st.text_area(" ", height=220, label_visibility="collapsed", key="descricao_txt")

        uploads=st.file_uploader("Fotos/Vídeos", key=f"uploads_{st.session_state['uploader_key']}", type=list({e.strip('.') for e in IMAGEM_EXTS|VIDEO_EXTS}), accept_multiple_files=True)
        ok=st.form_submit_button("Salvar Imóvel")

    if st.session_state.get("_saved_message"):
        st.success(st.session_state["_saved_message"])
        st.session_state.pop("_saved_message", None)

    if ok:
        # Proprietário
        if proprietario_id is None:
            if not novo_prop or not novo_prop["nome"]:
                st.error("Informe o nome do proprietário."); return
            proprietario_id = inserir_vendedor(
                novo_prop["nome"], novo_prop["email"], novo_prop["telefone"], novo_prop["creci"],
                novo_prop.get("rua"), novo_prop.get("numero"), novo_prop.get("complemento"),
                novo_prop.get("bairro"), novo_prop.get("cidade_estado"), novo_prop.get("cep")
            )
        # Imóvel
        valor = parse_brl(valor_str)
        if valor is None:
            st.error("Valor inválido. Use o formato 999.999,99."); return
        if not titulo: st.error("Informe o título."); return

        pid,cod=inserir_imovel({
            "titulo":titulo,"tipo":tipo,"valor":valor,"descricao":descricao,"quartos":quartos,"banheiros":banheiros,
            "vagas":vagas,"area":area,"rua":rua,"numero":numero,"complemento":complemento,"bairro":bairro,
            "cidade_estado":cidade_estado,"cep":cep,"vendedor_id":proprietario_id
        })
        save_uploaded_files(pid,uploads)
        st.session_state["_saved_message"] = f"Imóvel {cod} salvo com sucesso!"

        # Limpeza segura: marcar flag e reiniciar uploader, depois rerun.
        st.session_state["_clear_after_save"] = True
        st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1
        st.rerun()

def page_consulta():
    st.title("Consulta de Imóveis")
    imvs = listar_imoveis()
    if not imvs:
        st.info("Nenhum imóvel encontrado.")
        return

    # Busca textual
    q = st.text_input(
        "Buscar por título ou endereço (rua, bairro, cidade/estado, CEP)",
        placeholder="Ex.: Studio, Moema, Avenida Paulista, 04610-011",
        key="consulta_q"
    )

    # Filtra por título ou endereço
    if q:
        t = q.strip().lower()
        def _has(i, k):
            v = i.get(k, "")
            return t in str(v).lower() if v is not None else False
        filtrados = [i for i in imvs if (_has(i,"titulo") or _has(i,"rua") or _has(i,"bairro") or _has(i,"cidade_estado") or _has(i,"cep"))]
    else:
        filtrados = imvs

    # --> Sem tabela: apenas combo com resultados da busca
    st.caption(f"Resultados: {len(filtrados)} imóvel(is)")

    # --- resetar seleção se a lista mudou (hash simples) + select robusto por rótulo
    import hashlib, json
    def _hash_list(lst): 
        return hashlib.md5(json.dumps(lst, sort_keys=True, default=str).encode()).hexdigest()

    def _label_sel(i):
        rua = i.get('rua') or ''
        numero = i.get('numero') or ''
        bairro = i.get('bairro') or ''
        cid = i.get('cidade_estado') or ''
        end = f"{rua}, {numero} — {bairro} — {cid}".strip(' —,')
        return f"{i.get('codigo')} — {i.get('titulo')} — {end}"

    if not filtrados:
        st.warning("Nenhum imóvel encontrado para a busca. Refine os termos.")
        return

    labels = [_label_sel(i) for i in filtrados]
    mapa = {lab: imv for lab, imv in zip(labels, filtrados)}
    curr_hash = _hash_list(labels)

    if st.session_state.get("consulta_hash") != curr_hash:
        st.session_state["consulta_hash"] = curr_hash
        st.session_state["consulta_sel_label"] = None  # limpa seleção

    sel_label = st.selectbox(
        "Selecione o imóvel",
        options=labels,
        index=None if st.session_state.get("consulta_sel_label") is None else labels.index(st.session_state["consulta_sel_label"]) if st.session_state["consulta_sel_label"] in labels else None,
        placeholder="Escolha um imóvel…",
        key="consulta_sel_label"
    )

    # Detalhes
    st.markdown("---")
    st.subheader("Detalhes do Imóvel")

    if not sel_label:
        st.info("Escolha um imóvel para ver os detalhes.")
        return

    imv = mapa[sel_label]

    try:
        total_interessados = len(listar_interessados(imv["id"]))
        st.info(f"**Interessados neste imóvel:** {total_interessados}")

        cols = st.columns([2,3])
        with cols[0]:
            show_media_carousel(imv["id"])
        with cols[1]:
            st.markdown("### " + (imv.get("titulo") or "(Sem título)"))
            st.markdown(f"**Valor:** R$ {format_brl(imv.get('valor') or 0)}")
            st.write(imv.get("descricao") or "Sem descrição")
            st.markdown("---")
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Quartos", imv.get("quartos") or 0)
            c2.metric("Banheiros", imv.get("banheiros") or 0)
            c3.metric("Vagas", imv.get("vagas") or 0)
            c4.metric("Área (m²)", imv.get("area") or 0)
            st.markdown("---")
            st.write(f"**Endereço**: {imv.get('rua','')}, {imv.get('numero','')} — {imv.get('complemento','')}")
            st.write(f"**Bairro**: {imv.get('bairro','')}")
            st.write(f"**Cidade/Estado**: {imv.get('cidade_estado','')} — **CEP**: {imv.get('cep','')}")
            st.write(f"**Proprietário**: {imv.get('vendedor_nome') or '—'}")
            st.write(f"**Código**: {imv.get('codigo')}")
            st.write(f"**Cadastrado em**: {imv.get('data_cadastro')}")
    except Exception as e:
        st.error(f"Ocorreu um erro ao exibir os detalhes: {e}")
        st.exception(e)

def page_interessados():
    st.title("Interessados")
    imvs=listar_imoveis()
    if not imvs: 
        st.info("Cadastre um imóvel primeiro.")
        return

    termo = st.text_input("Buscar imóvel (código, título ou endereço)", placeholder="Ex.: IMO-0003, Avenida Paulista, Centro, São Paulo/SP, 01311-000")
    if termo:
        t = termo.strip().lower()
        def _has(i, k): 
            v = i.get(k, "")
            return t in str(v).lower() if v is not None else False
        filtrados = [i for i in imvs if (
            _has(i,"codigo") or _has(i,"titulo") or _has(i,"rua") or _has(i,"bairro") or _has(i,"cidade_estado") or _has(i,"cep")
        )]
    else:
        filtrados = imvs

    if not filtrados:
        st.warning("Nenhum imóvel encontrado para a busca.")
        return
    elif len(filtrados) == 1:
        imv = filtrados[0]
        st.success(f"Selecionado automaticamente: {imv.get('codigo')} — {imv.get('titulo')}")
    else:
        def _label(i):
            rua = i.get('rua') or ''
            numero = i.get('numero') or ''
            bairro = i.get('bairro') or ''
            cid = i.get('cidade_estado') or ''
            cep = i.get('cep') or ''
            end = f"{rua}, {numero} — {bairro} — {cid} — {cep}".strip(' —,')
            return f"{i.get('codigo')} — {i.get('titulo')} — {end}"
        opts = {_label(i): i for i in filtrados}
        chave = st.selectbox("Resultados da busca", list(opts.keys()))
        imv = opts[chave]

    qtd = len(listar_interessados(imv["id"]))
    st.markdown(f"**Interessados deste imóvel:** {qtd}")

    st.subheader("Novo interessado")
    with st.form("form_int",clear_on_submit=True):
        col1,col2 = st.columns(2)
        with col1:
            nome=st.text_input("Nome"); email=st.text_input("Email"); tel=st.text_input("Telefone")
            valor_proposto_str = st.text_input("Valor proposto (R$) — 999.999,99", placeholder="0,00")
        with col2:
            status=st.selectbox("Status",["Novo","Em contato","Proposta","Fechado"],index=0)
            msg=st.text_area("Mensagem inicial")
        ok=st.form_submit_button("Salvar interessado")
    if ok:
        if not nome: st.error("Informe o nome.")
        else:
            valor_prop = parse_brl(valor_proposto_str)
            if valor_prop is None:
                st.error("Valor proposto inválido. Use o formato 999.999,99.")
            else:
                inserir_interessado(imv["id"],nome,email,tel,msg,status,valor_prop)
                st.success("Interessado salvo!")

    st.markdown("---"); st.subheader("Interessados desse imóvel")
    regs=listar_interessados(imv["id"])
    if not regs:
        st.info("Nenhum interessado ainda."); return

    st.dataframe(pd.DataFrame([{
        "ID": r["id"],
        "Nome": r["nome"],
        "Email": r["email"],
        "Telefone": r["telefone"],
        "Status": r["status"],
        "Valor proposto (R$)": format_brl(r["valor_proposto"]),
        "Mensagem": r["mensagem"],
        "Data": r["data_interesse"],
    } for r in regs]), use_container_width=True, hide_index=True)

    st.subheader("Histórico de interações")
    opt_map = {f"{r['id']} — {r['nome']}": r for r in regs}
    escolha = st.selectbox("Escolha o interessado para gerenciar o histórico", list(opt_map.keys()))
    selecionado = opt_map[escolha]

    cA, cB = st.columns([2, 3])
    with cA:
        st.markdown(f"**Valor proposto:** R$ {format_brl(selecionado.get('valor_proposto'))}")
        st.markdown(f"**Status atual:** {selecionado['status']}")
        st.markdown(f"**Contato:** {selecionado['email']} — {selecionado['telefone']}")

    with st.form(f"form_interacao_{selecionado['id']}", clear_on_submit=True):
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data_evento = st.date_input("Data do evento", value=date.today())
        with dcol2:
            tipo_evento = st.selectbox(
                "Tipo de evento",
                ["Ligação", "Visita", "Compromisso", "Assinatura de contrato", "Envio de documentos", "Mensagem", "Outro"],
                index=0
            )
        observacao = st.text_area("O que foi tratado", placeholder="Descreva brevemente o que foi conversado...", height=120)
        ok2 = st.form_submit_button("Salvar evento")
    if ok2:
        inserir_interacao(selecionado["id"], data_evento, tipo_evento, observacao)
        st.success("Evento registrado!")

    historico = listar_interacoes(selecionado["id"])
    if historico:
        st.dataframe(pd.DataFrame([{
            "Data": h["data_evento"],
            "Evento": h["tipo_evento"],
            "Anotação": h["observacao"],
        } for h in historico]), use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma interação registrada para este interessado.")

# ================= Relatórios =================
def get_relatorio_df(vendedor_id: int|None=None) -> pd.DataFrame:
    imoveis = listar_imoveis({"vendedor_id": vendedor_id} if vendedor_id else None)
    base = pd.DataFrame(imoveis)
    if base.empty:
        return pd.DataFrame(columns=["Código","Título","Proprietário","Qtde interessados","Média proposta (R$)","Preço (R$)","Última interação"])

    conn = get_conn()
    inter = pd.read_sql_query("""
        SELECT i.*, it.data_evento as ultima_data_evento
        FROM interessados i
        LEFT JOIN (
            SELECT interessado_id, MAX(date(data_evento)) as data_evento
            FROM interacoes
            GROUP BY interessado_id
        ) it ON it.interessado_id = i.id
    """, conn)
    conn.close()

    if not inter.empty:
        inter["valor_proposto"] = pd.to_numeric(inter["valor_proposto"], errors="coerce")
        inter["ultima_data_evento"] = pd.to_datetime(inter["ultima_data_evento"], errors="coerce")

        aggs = inter.groupby("property_id").agg(
            qtd_interessados=("id","count"),
            media_proposta=("valor_proposto","mean"),
            ultima_interacao=("ultima_data_evento","max"),
        ).reset_index()
    else:
        aggs = pd.DataFrame(columns=["property_id","qtd_interessados","media_proposta","ultima_interacao"])

    df = base.merge(aggs, left_on="id", right_on="property_id", how="left")
    df["qtd_interessados"] = df["qtd_interessados"].fillna(0).astype(int)
    df["media_proposta"] = df["media_proposta"].fillna(0.0)
    if "ultima_interacao" in df.columns:
        df["ultima_interacao"] = pd.to_datetime(df["ultima_interacao"]).dt.strftime("%d/%m/%Y")
    else:
        df["ultima_interacao"] = "—"
    df["ultima_interacao"] = df["ultima_interacao"].fillna("—")

    df_out = pd.DataFrame({
        "Código": df["codigo"],
        "Título": df["titulo"],
        "Proprietário": df["vendedor_nome"],
        "Qtde interessados": df["qtd_interessados"],
        "Média proposta (R$)": df["media_proposta"].map(format_brl),
        "Preço (R$)": df["valor"].map(format_brl),
        "Última interação": df["ultima_interacao"],
    })
    return df_out

def page_relatorios():
    st.title("Relatórios")
    props = listar_vendedores()
    vend_map = {"Todos": None}
    for v in props: vend_map[f"{v['id']} - {v['nome']}"] = v["id"]
    vendedor_label = st.selectbox("Filtrar por proprietário", list(vend_map.keys()))
    vendedor_id = vend_map[vendedor_label]

    df = get_relatorio_df(vendedor_id)
    if df.empty:
        st.info("Sem dados para relatório.")
        return

    st.subheader("Resumo por imóvel")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Top imóveis por interessados")
    df_sorted = df.sort_values("Qtde interessados", ascending=False).head(10)
    st.bar_chart(df_sorted.set_index("Código")["Qtde interessados"])

    st.subheader("Exportar")
    csv_path = "relatorio_imoveis.csv"
    try:
        xlsx_path = "relatorio_imoveis.xlsx"
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Relatório")
        xlsx_link = "  |  [Baixar Excel](sandbox:/relatorio_imoveis.xlsx)"
    except Exception:
        xlsx_link = "  *(Excel indisponível — instale `openpyxl` ou `XlsxWriter` para habilitar)*"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    st.markdown(f"[Baixar CSV](sandbox:/relatorio_imoveis.csv){xlsx_link}")

# ================= Main =================
def main():
    init_db()
    st.sidebar.title("CRM Imobiliário")
    page=st.sidebar.radio(
        "Navegar",
        ["Cadastrar Imóvel","Consulta de Imóveis","Interessados","Relatórios"],
        index=1  # abre direto na consulta
    )
    if page=="Cadastrar Imóvel": page_cadastrar()
    elif page=="Consulta de Imóveis": page_consulta()
    elif page=="Interessados": page_interessados()
    else: page_relatorios()

if __name__=="__main__":
    main()
