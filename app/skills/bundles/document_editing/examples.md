# Document Editing – Extended Examples

## Example 1: Summarize a long document

**User**: "Summarize the report in D:\Documents\Q4_report.docx"

**Steps**:
1. `read_text_file("D:\\Documents\\Q4_report.docx")`
2. If the file is too large, use `summarize_document` instead.
3. Analyze the content and produce a structured summary with key findings.

---

## Example 2: Create a new file

**User**: "Create a Python script that reads a CSV and prints the top 5 rows."

**Steps**:
1. Write the script content.
2. `write_text_file("read_csv.py", <script_content>)`
3. Confirm to the user: "Created read_csv.py in the current directory."

---

## Example 3: Targeted edit in an existing file

**User**: "In config.yaml, change the port from 8080 to 3000."

**Steps**:
1. `read_text_file("config.yaml")` to see current content.
2. `propose_edit("config.yaml", find="port: 8080", replace="port: 3000")`
3. `apply_patch` with the generated patch.
4. Confirm: "Updated port from 8080 to 3000 in config.yaml."

---

## Example 4: Find and edit across multiple files

**User**: "Find all Python files that import 'flask' and change the import to 'fastapi'."

**Steps**:
1. `search_files("*.py", content_pattern="import flask")`
2. For each matching file:
   a. `read_text_file(file_path)`
   b. `propose_edit(file_path, find="import flask", replace="import fastapi")`
   c. `apply_patch`
3. Report the list of modified files.
