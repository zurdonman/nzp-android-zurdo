"""
Nazi Zombies: Portable QuakeC CRC generator

Takes input .CSV files and outputs an FTEQCC-compilable
QuakeC struct with its contents, always assumes the first
entry should be IBM 3740 CRC16 hashed, adding its length
as an entry as well, for collision detection.
"""

import argparse
import csv
import sys
import os
from dataclasses import dataclass

args = {}
struct_fields = []
original_lengths = []
original_names = []

ITYPE_FLOAT = 0
ITYPE_STRING = 1
ITYPE_CRC = 2


def crc16_ibm_3740(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class SimpleCSVData:
    def __init__(self, columns, values):
        self.columns = columns
        self.values = values


@dataclass
class StructField:
    name: str
    item_type: int = ITYPE_FLOAT


def write_qc_file(csv_data):
    with open(args['output_file'], 'w') as output:
        output.write('var struct {\n')
        for fields in struct_fields:
            if fields.item_type == ITYPE_STRING:
                output.write('string ')
            else:
                output.write('float ')
            output.write(f'{fields.name};\n')
        output.write('}')
        struct_name = args['struct_name']
        output.write(f'{struct_name}[]=')
        output.write('{\n')

        value_counter = 0
        for value in csv_data.values:
            output.write('{')
            entry_counter = 0
            for entry in value:
                if struct_fields[entry_counter].item_type != ITYPE_STRING:
                    output.write(f'{str(entry)},')
                else:
                    output.write(f'\"{entry}\",')
                entry_counter += 1

            output.write(str(original_lengths[value_counter]))
            output.write('}')
            if value_counter + 1 < len(csv_data.values):
                output.write(',')
            output.write(f' // {original_names[value_counter]}\n')
            value_counter += 1

        output.write('};\n')


def create_qc_structfields(csv_data):
    global struct_fields
    column_count = 0
    for column in csv_data.columns:
        if column_count == 0:
            item_type = ITYPE_CRC
            item_name = column + '_crc'
        else:
            item_type = ITYPE_STRING
            item_name = column
        struct_fields.append(StructField(item_name, item_type))
        column_count += 1
    struct_fields.append(StructField('crc_strlen', ITYPE_FLOAT))


def generate_qc_file(csv_data):
    create_qc_structfields(csv_data)
    write_qc_file(csv_data)


def read_csv_data():
    global original_lengths, original_names
    with open(args['input_file'], 'r', newline='') as f:
        reader = csv.reader(f)
        columns = [c.strip() for c in next(reader)]
        indexed_rows = []
        for idx, row in enumerate(reader):
            if not row:
                continue
            orig_name = row[0].strip()
            crc_val = crc16_ibm_3740(orig_name.encode('utf-8'))
            values = [crc_val] + [c.strip() for c in row[1:]]
            indexed_rows.append((crc_val, idx, orig_name, len(orig_name), values))

    indexed_rows.sort(key=lambda item: (item[0], item[1]))
    original_names = [item[2] for item in indexed_rows]
    original_lengths = [item[3] for item in indexed_rows]
    sorted_values = [item[4] for item in indexed_rows]
    return SimpleCSVData(columns, sorted_values)


def fetch_cli_arguments():
    global args
    parser = argparse.ArgumentParser(description='IBM 3740 CRC16 hash generator in FTE QuakeC-readable data structure.')
    parser.add_argument('-i', '--input-file',
                        help='.CSV input file to parse.', required=True)
    parser.add_argument('-o', '--output-file',
                        help='File name for generated .QC file.', default='hashes.qc')
    parser.add_argument('-s', '--struct-name',
                        help='Name for struct in generated .QC file.', default='asset_conversion_table')
    args = vars(parser.parse_args())


def main():
    fetch_cli_arguments()
    csv_data = read_csv_data()
    generate_qc_file(csv_data)


if __name__ == '__main__':
    main()
