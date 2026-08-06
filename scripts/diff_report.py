#!/usr/bin/env python3
"""
Generate a drift report between two metadata snapshots.

Usage:
    diff_report.py <left_dir> <right_dir> <left_label> <right_label> <manifests_dir> <output_html>

Both <left_dir> and <right_dir> must contain force-app/main/default/ subtrees.
The diff is filtered to only metadata that is explicitly referenced by the
manifests in <manifests_dir> — this hides out-of-scope content (e.g. unrelated
data kits already on the branch).
"""

import sys
import os
import glob
import hashlib
import html
import difflib
import xml.etree.ElementTree as ET

NS = 'http://soap.sforce.com/2006/04/metadata'

# Metadata type → (folder under force-app/main/default/, file suffix)
TYPE_TO_FILE = {
    'DataSource':                 ('mktDataSources',              '.dataSource-meta.xml'),
    'DataSourceBundleDefinition': ('dataSourceBundleDefinitions', '.dataSourceBundleDefinition-meta.xml'),
    'DataSourceObject':           ('dataSourceObjects',           '.dataSourceObject-meta.xml'),
    'DataSrcDataModelFieldMap':   ('dataSrcDataModelFieldMaps',   '.dataSrcDataModelFieldMap-meta.xml'),
    'ExtDataTranObjectTemplate':  ('extDataTranObjectTemplates',  '.extDataTranObjectTemplate-meta.xml'),
    'FieldSrcTrgtRelationship':   ('fieldSrcTrgtRelationships',   '.fieldSrcTrgtRelationship-meta.xml'),
}


def parse_manifests(manifests_dir):
    """Return a set of relative file paths (under force-app/main/default/)
    that are referenced by any manifest in manifests_dir."""
    expected = set()

    for manifest_path in sorted(glob.glob(os.path.join(manifests_dir, '*.xml'))):
        try:
            root = ET.parse(manifest_path).getroot()
        except ET.ParseError:
            continue

        for types_el in root.findall(f'{{{NS}}}types'):
            name_el = types_el.find(f'{{{NS}}}name')
            if name_el is None or not name_el.text:
                continue
            type_name = name_el.text.strip()
            members = [m.text.strip() for m in types_el.findall(f'{{{NS}}}members') if m.text]

            if type_name == 'CustomField':
                for m in members:
                    if '.' in m:
                        obj, field = m.split('.', 1)
                        expected.add(f'objects/{obj}/fields/{field}.field-meta.xml')
            elif type_name == 'CustomObject':
                for m in members:
                    expected.add(f'objects/{m}/{m}.object-meta.xml')
            elif type_name in TYPE_TO_FILE:
                folder, suffix = TYPE_TO_FILE[type_name]
                for m in members:
                    expected.add(f'{folder}/{m}{suffix}')
            # Other types are stripped before retrieve or out of scope for drift.

    return expected


def collect_files(root_dir, expected):
    """Walk root_dir/force-app/main/default and return a dict:
        {relative_path: sha256_hex}
    filtered to only paths in the expected set."""
    base = os.path.join(root_dir, 'force-app', 'main', 'default')
    result = {}
    if not os.path.isdir(base):
        return result
    for dirpath, _, filenames in os.walk(base):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, base)
            # Normalize to forward slashes
            rel = rel.replace(os.sep, '/')
            if rel not in expected:
                continue
            with open(full, encoding='utf-8', errors='replace') as f:
                content = normalize_content(rel, f.read())
            result[rel] = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return result


def normalize_content(rel, text):
    """Strip XML elements that Salesforce silently drops when metadata is received
    via deployment, even though the same element is present when metadata is created
    natively in an org.

    <externalDataTranField> is returned by Salesforce on retrieve when a field
    template is created directly in the dev org (mysdo-dev). When that same metadata
    is deployed to stage or prod, Salesforce normalizes it on ingest and no longer
    returns the element on subsequent retrieves. The value is always identical to
    <externalName>, making it redundant. Stripping it before hashing and diffing
    prevents false-positive drift in org-vs-branch and org-vs-org comparisons for
    deployed environments.
    """
    if 'extDataTranObjectTemplates/' in rel:
        import re
        text = re.sub(r'[ \t]*<externalDataTranField>[^<]*</externalDataTranField>\r?\n?', '', text)
        text = re.sub(r'[ \t]*<externalDataTranObject>[^<]*</externalDataTranObject>\r?\n?', '', text)
    return text


def read_text(root_dir, rel):
    full = os.path.join(root_dir, 'force-app', 'main', 'default', rel)
    try:
        with open(full, encoding='utf-8') as f:
            return normalize_content(rel, f.read())
    except (OSError, UnicodeDecodeError):
        return ''


def decode_field_map(left_dir, right_dir, rel):
    """For DataSrcDataModelFieldMap files, read the XML to extract source and target
    fields so we can show a human-readable form alongside the encoded name.
    Returns a string like "oneid_address_F4952E5B.City → Address_Extended_Search__dlm.City__c"
    or an empty string if the info can't be extracted."""
    if 'dataSrcDataModelFieldMaps' not in rel:
        return ''
    # Read from whichever side has the file
    for root in (left_dir, right_dir):
        full = os.path.join(root, 'force-app', 'main', 'default', rel)
        if os.path.isfile(full):
            try:
                tree = ET.parse(full)
                root_el = tree.getroot()
                src = root_el.find(f'{{{NS}}}sourceField')
                tgt = root_el.find(f'{{{NS}}}targetField')
                if src is not None and tgt is not None and src.text and tgt.text:
                    return f'{src.text.strip()} → {tgt.text.strip()}'
            except ET.ParseError:
                pass
            break
    return ''


def decode_file(left_dir, right_dir, rel):
    """Human-readable summary for a metadata file, when we can derive one.
    Returns an empty string when the raw name already speaks for itself."""
    return decode_field_map(left_dir, right_dir, rel)


def metadata_type(rel):
    """Derive a human-readable type label from the relative path."""
    parts = rel.split('/')
    if len(parts) >= 3 and parts[0] == 'objects' and parts[2] == 'fields':
        return 'CustomField'
    if parts[0] == 'objects':
        return 'CustomObject'
    # Fall back to the folder name, prettified
    folder = parts[0]
    return folder[0].upper() + folder[1:]


def short_name(rel):
    """Strip the -meta.xml suffix and metadata subfolder chatter for display."""
    parts = rel.split('/')
    name = parts[-1]
    for suffix in ('.dataSrcDataModelFieldMap-meta.xml',
                   '.dataSource-meta.xml',
                   '.dataSourceBundleDefinition-meta.xml',
                   '.dataSourceObject-meta.xml',
                   '.extDataTranObjectTemplate-meta.xml',
                   '.object-meta.xml',
                   '.field-meta.xml',
                   '-meta.xml'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    if len(parts) >= 3 and parts[0] == 'objects' and parts[2] == 'fields':
        return f'{parts[1]}.{name}'
    return name


def build_diff(left_text, right_text):
    diff = difflib.unified_diff(
        left_text.splitlines(keepends=False),
        right_text.splitlines(keepends=False),
        lineterm='',
        n=3,
    )
    return '\n'.join(diff)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Drift Report — {left_label} vs {right_label}</title>
<style>
  :root {{
    --bg: #fafafa; --card: #fff; --border: #e5e7eb;
    --text: #1f2937; --muted: #6b7280;
    --left: #2563eb; --right: #7c3aed;
    --added: #16a34a; --removed: #dc2626; --changed: #d97706;
    --green-bg: #ecfdf5;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--text); margin: 0; padding: 0; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }}
  .header {{ border-bottom: 2px solid var(--border); padding-bottom: 1.5rem; margin-bottom: 2rem; }}
  .header h1 {{ margin: 0 0 0.5rem; font-size: 1.5rem; font-weight: 600; }}
  .header .meta {{ color: var(--muted); font-size: 0.9rem; }}
  .labels {{ display: flex; gap: 2rem; margin-top: 1rem; font-size: 0.95rem; }}
  .labels .left {{ color: var(--left); }}
  .labels .right {{ color: var(--right); }}
  .labels strong {{ font-weight: 600; }}
  .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 2.5rem; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
           padding: 1.25rem; text-align: center; }}
  .card .count {{ font-size: 2rem; font-weight: 600; line-height: 1; }}
  .card .label {{ color: var(--muted); font-size: 0.85rem; margin-top: 0.5rem; }}
  .card.zero {{ opacity: 0.4; }}
  .card.removed .count {{ color: var(--removed); }}
  .card.added .count {{ color: var(--added); }}
  .card.changed .count {{ color: var(--changed); }}
  .no-drift {{ background: var(--green-bg); border: 1px solid #a7f3d0; border-radius: 8px;
               padding: 2.5rem; text-align: center; }}
  .no-drift h2 {{ margin: 0 0 0.5rem; color: #065f46; }}
  .no-drift p {{ margin: 0; color: #047857; }}
  section {{ margin-bottom: 2.5rem; }}
  section h2 {{ font-size: 1.1rem; font-weight: 600; margin: 0 0 1rem;
                padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }}
  .type-group {{ margin-bottom: 1.5rem; }}
  .type-group h3 {{ font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                    letter-spacing: 0.08em; color: var(--muted); margin: 0 0 0.5rem; }}
  ul.files {{ list-style: none; padding: 0; margin: 0; }}
  ul.files li {{ background: var(--card); border: 1px solid var(--border); border-radius: 6px;
                 padding: 0.5rem 0.9rem; margin-bottom: 0.35rem; font-size: 0.85rem; }}
  ul.files li.removed {{ border-left: 3px solid var(--removed); }}
  ul.files li.added {{ border-left: 3px solid var(--added); }}
  .readable {{ font-weight: 500; color: var(--text); word-break: break-word; }}
  .raw {{ display: block; font-family: "SF Mono", Menlo, monospace; font-size: 0.75rem;
          color: var(--muted); margin-top: 0.25rem; word-break: break-all; }}
  details.changed {{ background: var(--card); border: 1px solid var(--border); border-radius: 6px;
                     margin-bottom: 0.35rem; border-left: 3px solid var(--changed); }}
  details.changed > summary {{ cursor: pointer; padding: 0.5rem 0.9rem; font-size: 0.85rem; user-select: none; }}
  details.changed > summary:hover {{ background: #f9fafb; }}
  details.changed > summary .readable {{ display: block; }}
  pre.diff {{ background: #1e293b; color: #e2e8f0; padding: 1rem; margin: 0; overflow-x: auto;
              font-size: 0.8rem; line-height: 1.5; border-radius: 0 0 6px 6px; }}
  pre.diff .add {{ color: #86efac; }}
  pre.diff .del {{ color: #fca5a5; }}
  pre.diff .ctx {{ color: #94a3b8; }}
  pre.diff .hdr {{ color: #fbbf24; }}
  footer {{ margin-top: 3rem; color: var(--muted); font-size: 0.8rem; text-align: center; }}
  .limitation {{ margin-top: 2rem; padding: 0.9rem 1rem; border: 1px solid #fde68a;
                 background: #fffbeb; border-radius: 6px; color: #78350f;
                 font-size: 0.85rem; line-height: 1.5; }}
  .limitation strong {{ color: #92400e; }}
</style>
</head>
<body>
<div class="container">
  <header class="header">
    <h1>{title}</h1>
    <div class="meta">Generated {timestamp}{scope_note}</div>
    <div class="labels">
      <div class="left"><strong>Left:</strong> {left_label}</div>
      <div class="right"><strong>Right:</strong> {right_label}</div>
    </div>
  </header>
  {body}
  <div class="limitation">
    <strong>Known gap:</strong> destructive changes made in the Data Cloud UI (e.g. manually deleting a field mapping) are <strong>not</strong> detected by this report. The retrieve continues to return those components because the published data kit state still references them. To confirm a UI deletion, check Data Cloud Setup directly.
  </div>
  <footer>Scoped to {manifest_count} manifest{manifest_s} — {expected_count} expected file{expected_s}. Out-of-scope metadata is not shown.</footer>
</div>
</body>
</html>
"""


def render_diff(text):
    lines = []
    for ln in text.splitlines():
        esc = html.escape(ln)
        if ln.startswith('+++') or ln.startswith('---'):
            lines.append(f'<span class="hdr">{esc}</span>')
        elif ln.startswith('@@'):
            lines.append(f'<span class="hdr">{esc}</span>')
        elif ln.startswith('+'):
            lines.append(f'<span class="add">{esc}</span>')
        elif ln.startswith('-'):
            lines.append(f'<span class="del">{esc}</span>')
        else:
            lines.append(f'<span class="ctx">{esc}</span>')
    return '\n'.join(lines)


def group_by_type(items):
    groups = {}
    for rel in items:
        groups.setdefault(metadata_type(rel), []).append(rel)
    return groups


def render_file_entry(rel, left_dir, right_dir):
    """Returns a (readable, raw) HTML-escaped pair for displaying a file.
    If we can decode a human-readable form, readable = the decoded string and
    raw = the raw file name. Otherwise readable = the short file name and
    raw is empty (no second line)."""
    short = html.escape(short_name(rel))
    decoded = decode_file(left_dir, right_dir, rel)
    if decoded:
        return html.escape(decoded), short
    return short, ''


def render_file_list(items, css_class, left_dir, right_dir):
    lis = []
    for rel in sorted(items):
        full = html.escape(rel)
        readable, raw = render_file_entry(rel, left_dir, right_dir)
        raw_html = f'<span class="raw">{raw}</span>' if raw else ''
        lis.append(
            f'<li class="{css_class}" title="{full}">'
            f'<span class="readable">{readable}</span>{raw_html}'
            f'</li>'
        )
    return '<ul class="files">\n' + '\n'.join(lis) + '\n</ul>'


def render_changed_list(items, left_dir, right_dir):
    blocks = []
    for rel in sorted(items):
        full = html.escape(rel)
        readable, raw = render_file_entry(rel, left_dir, right_dir)
        raw_html = f'<span class="raw">{raw}</span>' if raw else ''
        diff_text = build_diff(read_text(left_dir, rel), read_text(right_dir, rel))
        rendered = render_diff(diff_text) if diff_text else '<span class="ctx">(binary or identical after normalization)</span>'
        blocks.append(
            f'<details class="changed">\n'
            f'  <summary title="{full}"><span class="readable">{readable}</span>{raw_html}</summary>\n'
            f'  <pre class="diff">{rendered}</pre>\n'
            f'</details>'
        )
    return '\n'.join(blocks)


def main():
    if len(sys.argv) != 7:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    left_dir, right_dir, left_label, right_label, manifests_dir, output_html = sys.argv[1:7]

    expected = parse_manifests(manifests_dir)
    if not expected:
        print(f'ERROR: no manifest members found in {manifests_dir}', file=sys.stderr)
        sys.exit(1)

    left_files = collect_files(left_dir, expected)
    right_files = collect_files(right_dir, expected)

    only_left = sorted(set(left_files) - set(right_files))
    only_right = sorted(set(right_files) - set(left_files))
    changed = sorted(rel for rel in set(left_files) & set(right_files)
                     if left_files[rel] != right_files[rel])

    import datetime
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    manifest_count = len(glob.glob(os.path.join(manifests_dir, '*.xml')))

    title = f'Drift Report: {html.escape(left_label)} vs {html.escape(right_label)}'

    if not only_left and not only_right and not changed:
        body = (
            '<div class="no-drift">\n'
            '<h2>No drift detected</h2>\n'
            f'<p>All {len(expected)} in-scope files match between the two sides.</p>\n'
            '</div>'
        )
    else:
        summary_cards = []
        for kind, count, label in [
            ('removed', len(only_left),  f'Only in {html.escape(left_label)}'),
            ('added',   len(only_right), f'Only in {html.escape(right_label)}'),
            ('changed', len(changed),    'Modified on both sides'),
        ]:
            zero = ' zero' if count == 0 else ''
            summary_cards.append(
                f'<div class="card {kind}{zero}">\n'
                f'  <div class="count">{count}</div>\n'
                f'  <div class="label">{label}</div>\n'
                f'</div>'
            )

        sections = []
        if only_left:
            groups = group_by_type(only_left)
            type_blocks = []
            for type_name in sorted(groups):
                type_blocks.append(
                    f'<div class="type-group">\n'
                    f'<h3>{html.escape(type_name)} ({len(groups[type_name])})</h3>\n'
                    f'{render_file_list(groups[type_name], "removed", left_dir, right_dir)}\n'
                    f'</div>'
                )
            sections.append(
                f'<section>\n<h2>Only in {html.escape(left_label)} ({len(only_left)})</h2>\n'
                + '\n'.join(type_blocks) + '\n</section>'
            )
        if only_right:
            groups = group_by_type(only_right)
            type_blocks = []
            for type_name in sorted(groups):
                type_blocks.append(
                    f'<div class="type-group">\n'
                    f'<h3>{html.escape(type_name)} ({len(groups[type_name])})</h3>\n'
                    f'{render_file_list(groups[type_name], "added", left_dir, right_dir)}\n'
                    f'</div>'
                )
            sections.append(
                f'<section>\n<h2>Only in {html.escape(right_label)} ({len(only_right)})</h2>\n'
                + '\n'.join(type_blocks) + '\n</section>'
            )
        if changed:
            groups = group_by_type(changed)
            type_blocks = []
            for type_name in sorted(groups):
                type_blocks.append(
                    f'<div class="type-group">\n'
                    f'<h3>{html.escape(type_name)} ({len(groups[type_name])})</h3>\n'
                    f'{render_changed_list(groups[type_name], left_dir, right_dir)}\n'
                    f'</div>'
                )
            sections.append(
                f'<section>\n<h2>Modified on both sides ({len(changed)})</h2>\n'
                + '\n'.join(type_blocks) + '\n</section>'
            )

        body = (
            '<div class="cards">\n' + '\n'.join(summary_cards) + '\n</div>\n'
            + '\n'.join(sections)
        )

    output = HTML_TEMPLATE.format(
        title=title,
        timestamp=timestamp,
        left_label=html.escape(left_label),
        right_label=html.escape(right_label),
        scope_note='',
        body=body,
        manifest_count=manifest_count,
        manifest_s='' if manifest_count == 1 else 's',
        expected_count=len(expected),
        expected_s='' if len(expected) == 1 else 's',
    )

    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(output)

    # Also print a terminal summary
    print()
    print('=' * 60)
    print(f'DRIFT REPORT: {left_label}  vs  {right_label}')
    print('=' * 60)
    print(f'  In-scope files:           {len(expected)}')
    print(f'  Only in {left_label}:     {len(only_left)}')
    print(f'  Only in {right_label}:    {len(only_right)}')
    print(f'  Modified on both sides:   {len(changed)}')
    print('=' * 60)
    if only_left or only_right or changed:
        print(f'HTML report: {output_html}')
    else:
        print('✓ No drift detected.')


if __name__ == '__main__':
    main()
