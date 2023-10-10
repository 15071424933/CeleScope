import sys
import subprocess
from collections import Counter

import pandas as pd
import pysam

from celescope.tools.__init__ import PATTERN_DICT
from celescope.__init__ import HELP_DICT
from celescope.tools.step import Step, s_common
from celescope.tools.barcode import Chemistry, Barcode
from celescope.tools import utils
from celescope.tools.make_ref import MakeRef
from celescope.tools.matrix import CountMatrix
from celescope.tools.emptydrop_cr import get_plot_elements

SAM_attributes = 'NH HI nM AS CR UR CB UB GX GN '

class Starsolo(Step):
    def __init__(self, args, display_title=None):
        Step.__init__(self, args, display_title=display_title)
        fq1_list = args.fq1.split(",")
        fq2_list = args.fq2.split(",")
        fq1_number = len(fq1_list)
        fq2_number = len(fq2_list)
        if fq1_number != fq2_number:
            sys.exit('fastq1 and fastq2 do not have same file number!')
        self.read_command = 'cat'
        if str(fq1_list[0]).endswith('.gz'):
            self.read_command = 'zcat'
        if args.chemistry == 'auto':
            ch = Chemistry(args.fq1)
            chemistry_list = ch.check_chemistry()
            if len(set(chemistry_list)) != 1:
                sys.exit('multiple chemistry found!' + str(chemistry_list))
            chemistry = chemistry_list[0]
        else:
            chemistry = args.chemistry
        
        if chemistry != 'customized':
            self.whitelist_str = " ".join(Chemistry.get_whitelist(chemistry))
            pattern = PATTERN_DICT[chemistry]
        else:
            self.whitelist_str = args.whitelist
            pattern = self.args.pattern
        self.cb_pos, self.umi_pos, self.umi_len = self.get_solo_pos(pattern)
        self.pattern = pattern

        if args.cell_calling_method == 'EmptyDrops_CR':
            self.cell_filter = 'EmptyDrops_CR'
        elif args.cell_calling_method == 'auto':
            self.cell_filter = 'CellRanger2.2'
       
        # output files
        solo_dir = f'{self.outdir}/{self.sample}_Solo.out/GeneFull_Ex50pAS'
        self.raw_matrix = f'{solo_dir}/raw'
        self.filtered_matrix = f'{solo_dir}/filtered'
        self.summary_file = f'{solo_dir}/Summary.csv'
        bam = f'{self.outdir}/{self.sample}_Aligned.sortedByCoord.out.bam'

        # outs
        self.outs = [self.raw_matrix, self.filtered_matrix, bam]
        
    
    @staticmethod
    def get_solo_pos(pattern):
        # returns: cb_pos, umi_pos, umi_len
        pattern_dict = Barcode.parse_pattern(pattern)
        cb_pos = ' '.join([f'0_{l}_0_{r-1}' for l, r in pattern_dict["C"]])
        if len(pattern_dict['U']) != 1:
            sys.exit(f'Error: Wrong pattern:{pattern}. \n Solution: fix pattern so that UMI only have 1 position.\n')
        ul, ur = pattern_dict["U"][0]
        umi_pos = f'0_{ul}_0_{ur-1}'
        umi_len = ur - ul
        return cb_pos, umi_pos, umi_len

    def run_starsolo(self):
        sa = SAM_attributes + self.args.SAM_attributes
        cmd = (
            'STAR \\\n'
            f'--genomeDir {self.args.genomeDir} \\\n'
            f'--readFilesIn {self.args.fq2} {self.args.fq1} \\\n'
            f'--readFilesCommand {self.read_command} \\\n'
            f'--soloCBwhitelist {self.whitelist_str} \\\n'
            f'--soloType CB_UMI_Complex \\\n'
            f'--soloCBposition {self.cb_pos} \\\n'
            f'--soloUMIposition {self.umi_pos} \\\n'
            f'--soloUMIlen {self.umi_len} \\\n'
            f'--soloCellFilter {self.cell_filter} \\\n'
            f'--outFileNamePrefix {self.out_prefix}_ \\\n'
            f'--runThreadN {self.thread} \\\n'
            f'--clip3pAdapterSeq {self.args.adapter_3p} \\\n'
            f'--outFilterMatchNmin {self.args.outFilterMatchNmin} \\\n'
            f'--outSAMattributes {sa} \\\n'
            '--soloCBmatchWLtype 1MM \\\n'
            '--soloFeatures Gene GeneFull_Ex50pAS \\\n'
            '--outSAMtype BAM SortedByCoordinate \\\n'
            '--soloCellReadStats Standard \\\n'
        )
        if self.args.STAR_param:
            cmd += self.args.STAR_param
        sys.stderr.write(cmd)
        subprocess.check_call(cmd, shell=True)

    @utils.add_log
    def gzip_matrix(self):
        cmd = f'gzip {self.raw_matrix}/*; gzip {self.filtered_matrix}/*'
        subprocess.check_call(cmd, shell=True)

    @utils.add_log
    def get_Q30_cb_UMI(self):
        fq1_list = self.args.fq1.split(",")
        pattern_dict = Barcode.parse_pattern(self.pattern)
        cb_10k, umi_10k, cb_10k_100k, umi_10k_100k = Counter(), Counter(), Counter(), Counter()
        n = 0
        with pysam.FastxFile(fq1_list[0], persist=False) as fq1:
            for entry in fq1:
                n += 1
                if n > 10 ** 5:
                    break
                qual = entry.quality
                cb_qual = Barcode.get_seq_str(qual, pattern_dict['C'])
                umi_qual = Barcode.get_seq_str(qual, pattern_dict['U'])
                if n <= 10 ** 4:
                    cb_10k.update(cb_qual)
                    umi_10k.update(umi_qual)
                else:
                    cb_10k_100k.update(cb_qual)
                    umi_10k_100k.update(umi_qual)
        
        cb_qual_counter = cb_10k
        umi_qual_counter = umi_10k
        if cb_10k_100k:
            cb_qual_counter = cb_10k_100k
            umi_qual_counter = umi_10k_100k
        q30_cb = sum([cb_qual_counter[k] for k in cb_qual_counter if k >= Barcode.ord2chr(
            30)]) / float(sum(cb_qual_counter.values()))
        q30_umi = sum([umi_qual_counter[k] for k in umi_qual_counter if k >= Barcode.ord2chr(
            30)]) / float(sum(umi_qual_counter.values()))
        return q30_cb, q30_umi


    def run(self):
        self.run_starsolo()
        self.gzip_matrix()
        q30_cb, q30_umi = self.get_Q30_cb_UMI()
        return q30_cb, q30_umi


def starsolo(args):

    with Starsolo(args) as runner:
        q30_cb, q30_umi = runner.run()

    with Mapping(args) as runner:
        valid_reads = runner.run()
    
    with Cells(args) as runner:
        n_reads, q30_RNA = runner.run(valid_reads)
    
    with Demultiplexing(args) as runner:
        runner.run(valid_reads, n_reads, q30_cb, q30_umi, q30_RNA)


class Mapping(Step):
    # only add metrics
    def __init__(self, args, display_title=None):
        super().__init__(args, display_title=display_title)
        solo_dir = f'{self.outdir}/{self.sample}_Solo.out/GeneFull_Ex50pAS'
        self.cellReadsStats = f'{solo_dir}/CellReads.stats'
        self.filtered_matrix = f'{self.outs_dir}/filtered'
        self.UMI_counts_file = f'{solo_dir}/UMI_Counts.txt'
        self.genome = MakeRef.get_config(args.genomeDir)['meta']['genome_name']


    @utils.add_log
    def run(self):
        df = pd.read_csv(self.cellReadsStats, sep='\t', header=0, index_col=0)
        df = df.iloc[1:,] # skip first line cb not pass whitelist
        df_UMI = df.loc[:,'nUMIunique'].to_frame() # keep dataframe format
        df_UMI.rename(columns={'nUMIunique': 'UMI'}, inplace=True)
        df = df.loc[:,['cbMatch','genomeU', 'genomeM', 'exonic', 'intronic','exonicAS','intronicAS','countedU']]
        s= df.sum()
        # json does not recognize NumPy data types. TypeError: Object of type int64 is not JSON serializable
        valid = int(s['cbMatch'])
        genomeU= int(s['genomeU'])
        genomeM = int(s['genomeM'])
        exonic = int(s['exonic'])
        intronic = int(s['intronic'])
        antisense = int(s['exonicAS'] + s['intronicAS'])
        intergenic = genomeM + genomeU - exonic - intronic - antisense
        countedU = int(s['countedU'])
        del df

        self.add_metric(
            name='genome',
            value=self.genome,
        )

        self.add_metric(
            name='Reads mapped to unique loci',
            value=genomeU / valid,
            value_type='fraction',
            help_info='Reads that mapped uniquely to the genome.'
        )

        self.add_metric(
            name='Reads mapped to multiple loci',
            value=genomeM / valid,
            value_type='fraction',
            help_info='Reads that mapped to multiple loci in the genome'
        )
        self.add_metric(
            name='Reads mapped uniquely to Transcriptome',
            value=countedU / valid,
            value_type='fraction',
            help_info='Reads that mapped to a unique gene in the transcriptome. These reads are used for UMI counting.'
        )
        self.add_metric(
            name='Reads assigned to exonic regions',
            value=exonic / valid,
            value_type='fraction',
            help_info='Reads that assigned to exonic regions of genes',
        )
        self.add_metric(
            name='Reads assigned to intronic regions',
            value=intronic / valid,
            value_type='fraction',
            help_info='Reads that assigned to intronic regions of genes',
        )
        self.add_metric(
            name='Reads assigned to intergenic regions',
            value=intergenic / valid,
            value_type='fraction',
            help_info='Reads that can not be assigned to a gene will be considered as intergenic reads.',
        )
        self.add_metric(
            name='Reads assigned Antisense to gene',
            value=antisense / valid,
            value_type='fraction',
            help_info='Reads that assigned to the opposite strand of genes',
        )

        df_UMI.sort_values(by='UMI', ascending=False, inplace=True)
        cbs = CountMatrix.read_barcodes(self.filtered_matrix)
        df_UMI['mark'] = 'UB'
        for cb in cbs:
            df_UMI.loc[cb, 'mark'] = 'CB'
        df_UMI.to_csv(self.UMI_counts_file, sep='\t', index=True)

        return valid


class Cells(Step):
    def __init__(self, args, display_title=None):
        super().__init__(args, display_title=display_title)
        solo_dir = f'{self.outdir}/{self.sample}_Solo.out/GeneFull_Ex50pAS'
        self.summary_file = f'{solo_dir}/Summary.csv'
        self.UMI_counts_file = f'{solo_dir}/UMI_Counts.txt'


    @utils.add_log
    def parse_summary_add_metrics(self, valid_reads):
        df = pd.read_csv(self.summary_file, index_col=0, header=None)
        s = df.iloc[:,0]
        n_cells = int(s["Estimated Number of Cells"])
        fraction_reads_in_cells = float(s["Fraction of Unique Reads in Cells"])
        mean_reads_per_cell = valid_reads // n_cells
        median_umi_per_cell  = int(s["Median UMI per Cell"])
        median_genes_per_cell = int(s["Median GeneFull_Ex50pAS per Cell"])
        total_genes = int(s["Total GeneFull_Ex50pAS Detected"])
        saturation = float(s["Sequencing Saturation"])
        n_reads = int(s["Number of Reads"])
        q30_RNA = float(s["Q30 Bases in RNA read"])

        self.add_metric(
            name='Estimated Number of Cells',
            value=n_cells,
            help_info='the number of barcodes considered as cell-associated.'
        )

        fraction_reads_in_cells = round(fraction_reads_in_cells * 100, 2)
        self.add_metric(
            name='Fraction Reads in Cells',
            value=fraction_reads_in_cells,
            display=f'{fraction_reads_in_cells}%',
            help_info='the fraction of uniquely-mapped-to-transcriptome reads with cell-associated barcodes'
        )

        self.add_metric(
            name='Mean Reads per Cell',
            value=mean_reads_per_cell,
            help_info='the number of valid reads divided by the estimated number of cells'
        )

        self.add_metric(
            name='Median UMI per Cell',
            value=median_umi_per_cell,
            help_info='the median number of UMI counts per cell-associated barcode'
        )

        self.add_metric(
            name='Total Genes',
            value=total_genes,
            help_info='the number of genes with at least one UMI count in any cell'
        )

        self.add_metric(
            name='Median Genes per Cell',
            value=median_genes_per_cell,
            help_info='the median number of genes detected per cell-associated barcode'
        )

        saturation = round(saturation * 100, 2)
        self.add_metric(
            name='Saturation',
            value=saturation,
            display=f'{saturation}%',
            help_info='the fraction of read originating from an already-observed UMI. '
        )
        return n_reads, q30_RNA

    def run(self, valid_reads):
        n_reads, q30_RNA = self.parse_summary_add_metrics(valid_reads)
        self.add_data(chart=get_plot_elements.plot_barcode_rank(self.UMI_counts_file))
        return n_reads, q30_RNA


class Demultiplexing(Step):
    def __init__(self, args, display_title=None):
        super().__init__(args, display_title=display_title)

    def run(self, valid_reads, n_reads, q30_cb, q30_umi, q30_RNA):
        self.add_metric(
            name='Raw Reads',
            value=n_reads,
            help_info='total reads from FASTQ files'
        )
        self.add_metric(
            name='Valid Reads',
            value=valid_reads / n_reads,
            value_type='fraction',
            help_info='reads with valid barcode and UMI'
        )

        self.add_metric(
            name='Q30 of Barcodes',
            value=q30_cb,
            value_type='fraction',
            help_info='Fraction of barcode bases with quality score >= 30',
        )

        self.add_metric(
            name='Q30 of UMI',
            value=q30_umi,
            value_type='fraction',
            help_info='Fraction of UMI bases with quality score >= 30',
        )

        self.add_metric(
            name='Q30 of RNA Reads',
            value=q30_RNA,
            value_type='fraction',
            help_info='Fraction of RNA read bases with quality score >= 30',
        )


def get_opts_starsolo(parser, sub_program=True):
    parser.add_argument(
        '--chemistry',
        help='Predefined (pattern, barcode whitelist, linker whitelist) combinations. ' + HELP_DICT['chemistry'],
        choices=list(PATTERN_DICT.keys()),
        default='auto'
    )
    parser.add_argument(
        '--pattern',
        help="""The pattern of R1 reads, e.g. `C8L16C8L16C8L1U12T18`. The number after the letter represents the number 
        of bases.  
        - `C`: cell barcode  
        - `L`: linker(common sequences)  
        - `U`: UMI    
        - `T`: poly T""",
    )
    parser.add_argument(
        '--whitelist',
        help='Cell barcode whitelist file path, one cell barcode per line.'
    )
    parser.add_argument(
        '--adapter_3p',
        help='Adapter sequence to clip from 3 prime. Multiple sequences are seperated by space',
        default='AAAAAAAAAAAA',
    )
    parser.add_argument(
        '--genomeDir',
        help=HELP_DICT['genomeDir'],
    )
    parser.add_argument(
        '--outFilterMatchNmin',
        help="""Alignment will be output only if the number of matched bases 
is higher than or equal to this value.""",
        default=50,
    )
    parser.add_argument(
        '--cell_calling_method',
        help=HELP_DICT['cell_calling_method'],
        choices=['auto', 'EmptyDrops_CR'],
        default='EmptyDrops_CR',
    )
    parser.add_argument(
        '--starMem',
        help='Maximum memory that STAR can use.',
        default=32
    )
    parser.add_argument('--STAR_param', help=HELP_DICT['additional_param'], default="")
    parser.add_argument(
        '--SAM_attributes', 
        help=f'Additional attributes(other than {SAM_attributes}) to be added to SAM file',
        default="")
    if sub_program:
        parser.add_argument('--fq1', help='R1 fastq file. Multiple files are separated by comma.', required=True)
        parser.add_argument('--fq2', help='R2 fastq file. Multiple files are separated by comma.', required=True)
        parser = s_common(parser)

    return parser