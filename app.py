

import os
import shutil
import zipfile
import fitz  # PyMuPDF para extraer imágenes del PDF
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session, flash
import pandas as pd
import numpy as np  # <-- Aquí se importa numpy
import cv2
from pyzbar.pyzbar import decode
import base64
import json

app = Flask(__name__, static_folder='static')
app.secret_key = 'your_secret_key'  # Es importante definir una clave secreta para la gestión de sesiones

UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'
USER_FILE = 'users.json'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

# Función para cargar los usuarios desde el archivo 'users.json'
def load_users():
    if not os.path.exists(USER_FILE):
        with open(USER_FILE, 'w') as f:
            json.dump({}, f)  # Si no existe el archivo, creamos uno vacío
    with open(USER_FILE, 'r') as f:
        return json.load(f)

# Función para guardar los usuarios en 'users.json'
def save_users(users):
    with open(USER_FILE, 'w') as f:
        json.dump(users, f)

# Ruta principal (login requerido)
@app.route('/')
def index():
    # Si el usuario está logueado, mostrar la página principal
    if 'logged_in' in session:
        return render_template('index.html')
    return redirect(url_for('login'))

# Ruta de inicio de sesión
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = load_users()
        
        # Validar las credenciales del usuario
        if username in users and users[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            flash('Usuario o contraseña incorrectos', 'error')
            return redirect(url_for('login'))
    return render_template('login.html')

# Ruta de registro de usuario
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = load_users()

        if username in users:
            flash('El usuario ya existe. Elige otro nombre de usuario.', 'error')
            return redirect(url_for('register'))

        users[username] = password  # Añadir nuevo usuario
        save_users(users)
        flash('Usuario registrado exitosamente. Ahora puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

# Ruta para cerrar sesión
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

# --- Funciones de procesamiento de archivos se mantienen igual ---
# Código que ya funciona sin tocar (funciones de upload, rename, export_excel, etc.).

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No se recibieron archivos"}), 400

    uploaded_files = request.files.getlist("file")
    action = request.form.get('action')

    processed_files = []
    errores = []
    all_data_list = []  # Lista para acumular datos para exportar a Excel
    all_errors = []  # Lista de archivos con errores
    all_success = []  # Lista de archivos procesados correctamente

    # Procesar cada archivo subido
    for file in uploaded_files:
        filename = file.filename
        destination = os.path.join(UPLOAD_FOLDER, filename)

        # Guardar el archivo subido en la carpeta 'uploads'
        try:
            file.save(destination)
        except Exception as e:
            errores.append(f"Error al guardar {filename}: {str(e)}")
            continue  # Saltar al siguiente archivo si hubo error

        # Realizar la acción seleccionada
        if action == 'rename':
            result = process_and_rename(destination)  # Procesar y renombrar
        elif action == 'export_excel':
            result = process_and_export_excel(destination, all_data_list)  # Acumular datos en all_data_list
        else:
            result = {"status": "error", "message": "Acción no válida."}

        if result['status'] == 'success':
            # Añadir el nuevo archivo renombrado o procesado a la lista de archivos procesados
            processed_files.append(result.get('file', ''))
            all_success.append(result.get('file', ''))  # Usar el nuevo nombre
        else:
            errores.append(result['message'])
            all_errors.append(filename)

    # Si se trata de exportar a Excel y hay datos acumulados, crear el archivo Excel final
    if action == 'export_excel' and all_data_list:
        output_filename = 'merged_output_data.xlsx'
        output_path = os.path.join(PROCESSED_FOLDER, output_filename)
        df = pd.DataFrame(all_data_list)
        df.to_excel(output_path, index=False)
        processed_files.append(output_filename)

    # Generar el archivo de resultados con errores y archivos procesados correctamente
    results_txt = generate_results_file(all_errors, all_success)
    processed_files.append(results_txt)

    # Crear un archivo ZIP que contenga todos los archivos procesados
    zip_filename = "processed_files.zip"
    zip_filepath = os.path.join(PROCESSED_FOLDER, zip_filename)

    # Crear el archivo ZIP y agregar todos los archivos procesados y el archivo de texto
    with zipfile.ZipFile(zip_filepath, 'w') as zipf:
        for file_name in processed_files:
            file_path = os.path.join(PROCESSED_FOLDER, file_name)
            if os.path.exists(file_path):
                zipf.write(file_path, os.path.basename(file_path))
            else:
                errores.append(f"No se encontró el archivo {file_name} para agregar al ZIP.")

    # Enviar el archivo ZIP para su descarga
    return jsonify({"status": "success", "download_link": f"/download/{zip_filename}", "errors": errores})

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(PROCESSED_FOLDER, filename), as_attachment=True)

# Función para renombrar archivos PDF
def process_and_rename(pdf_path):
    try:
        images = extract_images_from_pdf(pdf_path)
        json_data = None

        for image in images:
            qr_data_list = detect_qr_code(image)
            if qr_data_list:
                for qr_data in qr_data_list:
                    json_data = decode_base64_qr(qr_data)
                    if json_data:
                        break
            if json_data:
                break

        if json_data:
            # Extraer los campos necesarios del JSON y formatearlos con ceros a la izquierda
            cuit = str(json_data.get("cuit", "0")).zfill(8)
            tipo_cmp = str(json_data.get("tipoCmp", "0")).zfill(3)
            pto_vta = str(json_data.get("ptoVta", "0")).zfill(5)
            nro_cmp = str(json_data.get("nroCmp", "0")).zfill(8)

            # Crear el nombre del archivo con el formato requerido
            nuevo_nombre = f"{cuit}-{tipo_cmp}-{pto_vta}-{nro_cmp}.pdf"
            nuevo_nombre_path = os.path.join(PROCESSED_FOLDER, nuevo_nombre)

            # Renombrar y mover el archivo al directorio de salida
            shutil.move(pdf_path, nuevo_nombre_path)
            return {"status": "success", "file": nuevo_nombre}
        else:
            return {"status": "error", "message": f"No se detectaron códigos QR válidos en {pdf_path}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Función para exportar a Excel
def process_and_export_excel(pdf_path, all_data_list):
    try:
        images = extract_images_from_pdf(pdf_path)
        data_list = []

        for image in images:
            qr_data_list = detect_qr_code(image)
            if qr_data_list:
                for qr_data in qr_data_list:
                    json_data = decode_base64_qr(qr_data)
                    if json_data:
                        data_list.append(json_data)

        all_data_list.extend(data_list)

        if data_list:
            return {"status": "success"}
        else:
            return {"status": "error", "message": "No se detectaron códigos QR válidos en el archivo."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Función para generar el archivo de texto con los resultados
def generate_results_file(errors, success):
    results_path = os.path.join(PROCESSED_FOLDER, "operation_results.txt")
    with open(results_path, "w") as results_file:
        if errors:
            results_file.write("Archivos con error:\n")
            for err_file in errors:
                results_file.write(f"- {err_file}\n")
        else:
            results_file.write("No hubo errores en el procesamiento.\n")
        results_file.write("\n")
        if success:
            results_file.write("Archivos procesados correctamente:\n")
            for success_file in success:
                results_file.write(f"- {success_file}\n")
    return "operation_results.txt"

# Funciones auxiliares
def extract_images_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    images = []
    for page_num in range(doc.page_count):
        if page_num == 0:
            page = doc.load_page(page_num)
            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                images.append(image_bytes)
    return images

def detect_qr_code(image_data):
    np_arr = np.frombuffer(image_data, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    qr_codes = decode(img)
    return [qr.data.decode('utf-8') for qr in qr_codes]

def decode_base64_qr(qr_data):
    if "afip.gob.ar/fe/qr/" in qr_data and "?p=" in qr_data:
        base64_data = qr_data.split("?p=")[-1]
        try:
            decoded_data = base64.b64decode(base64_data).decode("utf-8")
            return json.loads(decoded_data)
        except Exception:
            return None
    return None

if __name__ == "__main__":
    app.run(debug=True, port=81)
