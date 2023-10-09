#!/usr/bin/env python3

"""
This script generates a custom template database for AlphaFold2 from PDB or mmCIF
template files.
Removes steric clashes and low pLDDT regions from the template files.
Can be used as a standalone script.

"""

import os
import shutil
import sys
from pathlib import Path
from absl import logging, flags, app
from alphapulldown.remove_clashes_low_plddt import MmcifChainFiltered
from colabfold.batch import validate_and_fix_mmcif, convert_pdb_to_mmcif
from alphafold.common.protein import _from_bio_structure, to_mmcif
from Bio import SeqIO, PDB

FLAGS = flags.FLAGS


def save_seqres(code, chain, s, path):
    """
    o code - four letter PDB-like code
    o chain - chain ID
    o s - sequence
    o path - path to the pdb_seqresi, unique for each chain
    Returns:
        o Path to the file
    """
    fn = path / 'pdb_seqres.txt'
    # Rewrite the file if it exists
    with open(fn, 'a') as f:
        lines = f">{code}_{chain} mol:protein length:{len(s)}\n{s}\n"
        logging.info(f'Saving SEQRES for chain {chain} to {fn}!')
        logging.debug(lines)
        f.write(lines)
    return fn


def parse_code(template):
    # Check that the code is 4 letters
    code = Path(template).stem
    with open(template, "r") as f:
        for line in f:
            if line.startswith("_entry.id"):
                code = line.split()[1]
                if len(code) != 4:
                    logging.error(f'Error for template {template}!\n'
                                  f'Code must have 4 characters but is {code}\n')
                    sys.exit(1)
    return code.lower()


def create_dir_and_remove_files(dir_path, files_to_remove=[]):
    try:
        Path(dir_path).mkdir(parents=True)
    except FileExistsError:
        logging.info(f"{dir_path} already exists!")
        logging.info("The existing database will be overwritten!")
        for f in files_to_remove:
            target_file = dir_path / Path(f)
            if target_file.exists():
                target_file.unlink()


def create_tree(pdb_mmcif_dir, mmcif_dir, seqres_dir, templates_dir):
    """
    Create the db structure with empty directories
    o pdb_mmcif_dir - path to the output directory
    o mmcif_dir - path to the mmcif directory
    o seqres_dir - path to the seqres directory
    o templates_dir - path to the directory with all-chain templates in mmcif format
    Returns:
        o None
    """
    if Path(pdb_mmcif_dir).exists():
        files_to_remove = os.listdir(pdb_mmcif_dir)
    else:
        files_to_remove = []
    create_dir_and_remove_files(mmcif_dir, files_to_remove)
    create_dir_and_remove_files(templates_dir)

    # Create empty obsolete.dat file
    with open(pdb_mmcif_dir / 'obsolete.dat', 'a'):
        pass

    create_dir_and_remove_files(seqres_dir, ['pdb_seqres.txt'])


def extract_seqs(template, chain_id):
    """
    Extract sequences from PDB/CIF file using Bio.SeqIO.
    o input_file_path - path to the input file
    o chain_id - chain ID
    Returns:
        o sequence_atom - sequence from ATOM records
        o sequence_seqres - sequence from SEQRES records
    """
    file_type = template.suffix.lower()

    if template.suffix.lower() != '.pdb' and template.suffix.lower() != '.cif':
        raise ValueError(f"Unknown file type for {template}!")

    format_types = [f"{file_type[1:]}-atom", f"{file_type[1:]}-seqres"]
    # initialize the sequences
    sequence_atom = None
    sequence_seqres = None
    # parse
    for format_type in format_types:
        for record in SeqIO.parse(template, format_type):
            chain = record.annotations['chain']
            if chain == chain_id:
                if format_type.endswith('atom'):
                    sequence_atom = str(record.seq)
                elif format_type.endswith('seqres'):
                    sequence_seqres = str(record.seq)
    if sequence_atom is None:
        logging.error(f"No atom sequence found for chain {chain_id}")
    if sequence_seqres is None:
        logging.warning(f"No SEQRES sequence found for chain {chain_id}")
    return sequence_atom, sequence_seqres


def create_db(out_path, templates, chains, threshold_clashes, hb_allowance, plddt_threshold):
    """
    Main function that creates a custom template database for AlphaFold2
    from a PDB/CIF template files.
    o out_path - path to the output directory where the database will be created
    o templates - list of paths to the template files
    o chains - list of chain IDs of the multimeric templates
    o threshold_clashes - threshold for clashes removal
    o hb_allowance - hb_allowance for clashes removal
    o plddt_threshold - threshold for low pLDDT regions removal
    Returns:
        o None
    """
    # Create the database structure
    pdb_mmcif_dir = Path(out_path) / 'pdb_mmcif'
    mmcif_dir = pdb_mmcif_dir / 'mmcif_files'
    seqres_dir = Path(out_path) / 'pdb_seqres'
    templates_dir = Path(out_path) / 'templates'
    create_tree(pdb_mmcif_dir, mmcif_dir, seqres_dir, templates_dir)
    # Process each template/chain pair
    for template, chain_id in zip(templates, chains):
        code = parse_code(template)
        # Copy the template to out_path to avoid conflicts with the same file names
        shutil.copyfile(template, templates_dir / Path(template).name)
        template = templates_dir / Path(template).name
        logging.info(f"Processing template: {template}  Chain {chain_id} Code: {code}")
        logging.info("Parsing SEQRES...")
        atom_seq, seqres_seq = None, None
        if template.suffix == '.pdb':
            atom_seq, seqres_seq = extract_seqs(template, chain_id)
            logging.info(f"Converting to mmCIF: {template}")
            template = Path(template)
            convert_pdb_to_mmcif(template)
            template = template.parent.joinpath(f"{template.stem}.cif")
        # Convert to (our) mmcif object
        mmcif_obj = MmcifChainFiltered(template, code, chain_id)
        # Parse SEQRES
        if mmcif_obj.sequence_seqres:
            seqres = mmcif_obj.sequence_seqres
        else:
            seqres = mmcif_obj.sequence_atom
        # if we converted from pdb, seqres is parsed from Bio.SeqIO
        if seqres_seq or atom_seq:
            seqres = seqres_seq
            if seqres is None:
                seqres = atom_seq
        sqrres_path = save_seqres(code, chain_id, seqres, seqres_dir)
        logging.info(f"SEQRES saved to {sqrres_path}!")
        # Remove clashes and low pLDDT regions for each template
        mmcif_obj.remove_clashes(threshold_clashes, hb_allowance)
        mmcif_obj.remove_low_plddt(plddt_threshold)
        #Get atom site label seq ids
        atom_site_label_seq_ids = mmcif_obj.extract_atom_site_label_seq_id()
        # Convert to Protein
        protein = _from_bio_structure(mmcif_obj.structure)
        # Convert to mmCIF
        mmcif_string = to_mmcif(protein,
                                f"{code}_{chain_id}",
                                "Monomer",
                                chain_id,
                                seqres,
                                atom_site_label_seq_ids)
        # Save to file
        fn = mmcif_dir / f"{code}.cif"
        with open(fn, 'w') as f:
            f.write(mmcif_string)
        # Fix and validate with ColabFold
        validate_and_fix_mmcif(fn)
        logging.info(f'{template} is done!')




def main(argv):
    flags.FLAGS(argv)
    create_db(flags.FLAGS.out_path, [flags.FLAGS.template], [flags.FLAGS.multimeric_chain],
              flags.FLAGS.threshold_clashes, flags.FLAGS.hb_allowance, flags.FLAGS.plddt_threshold)


if __name__ == '__main__':
    flags.DEFINE_string("out_path", None, "Path to the output directory")
    flags.DEFINE_string("template", None, "Path to the template mmCIF/PDB file")
    flags.DEFINE_string("multimeric_chain", None, "Chain ID of the multimeric template")
    flags.DEFINE_float("threshold_clashes", 1000, "Threshold for VDW overlap to identify clashes "
                                                  "(default: 1000, i.e. no threshold, for thresholding, use 0.9)")
    flags.DEFINE_float("hb_allowance", 0.4, "Allowance for hydrogen bonding (default: 0.4)")
    flags.DEFINE_float("plddt_threshold", 0, "Threshold for pLDDT score (default: 0)")
    flags.mark_flags_as_required(["out_path", "template", "multimeric_chain"])
    app.run(main)
