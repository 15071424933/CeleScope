from celescope.dynaseq.__init__ import __ASSAY__
from celescope.tools.multi import Multi
from celescope.tools.__init__ import FILTERED_MATRIX_DIR_SUFFIX, BARCODE_FILE_NAME, TAG_BAM_SUFFIX


class Multi_dynaseq(Multi):

    """
    ## Usage

    ```
        multi_dynaseq\\
        --mapfile ./rna.mapfile\\
        --genomeDir /SGRNJ/Public/Database/genome/homo_mus\\
    ```

    For control sample, set --control to skip replacement step.
    ```
        multi_dynaseq\\
        --mapfile ./rna.mapfile\\
        --genomeDir /SGRNJ/Public/Database/genome/homo_mus\\
        --control
    ```
    """

    def star(self, sample):
        """
        """
        step = 'star'
        fq = f'{self.outdir_dic[sample]["cutadapt"]}/{sample}_clean_2.fq{self.fq_suffix}'
        cmd_line = self.get_cmd_line(step, sample)
        cmd = (
            f'{cmd_line} '
            f'--fq {fq} '
            f'--STAR_param "--outFilterScoreMinOverLread 0.3 --outFilterMatchNminOverLread 0.3 --outSAMattributes MD NH HI AS nM" '
        )
        self.process_cmd(cmd, step, sample, m=self.args.starMem, x=self.args.thread)

    def prep_map(self, sample):
        step = 'prep_map'
        arr = self.fq_dict[sample]
        cmd_line = self.get_cmd_line(step, sample)
        cmd = (
            f'{cmd_line} '
            f'--fq1 {arr[0]} --fq2 {arr[1]} '
            f'--STAR_param "--outFilterScoreMinOverLread 0.3 --outFilterMatchNminOverLread 0.3 --outSAMattributes MD NH HI AS nM"'
        )
        self.process_cmd(cmd, step, sample, m=self.args.starMem, x=self.args.thread)

    def conversion(self, sample):
        step = 'conversion'
        bam = f'{self.outdir_dic[sample]["featureCounts"]}/{sample}_{TAG_BAM_SUFFIX}'
        cell = f'{self.outdir_dic[sample]["count"]}/{sample}_{FILTERED_MATRIX_DIR_SUFFIX[0]}/{BARCODE_FILE_NAME}'
        cmd_line = self.get_cmd_line(step, sample)
        cmd = (
            f'{cmd_line} '
            f'--bam {bam} '
            f'--cell {cell} '
        )
        self.process_cmd(cmd, step, sample, m=200, x=self.args.thread)

    def substitution(self, sample):
        step = 'substitution'
        bam = f'{self.outdir_dic[sample]["conversion"]}/{sample}.PosTag.bam'
        cmd_line = self.get_cmd_line(step, sample)
        cmd = (
            f'{cmd_line} '
            f'--bam {bam} '
        )
        self.process_cmd(cmd, step, sample, m=1, x=1)

    def replacement(self, sample):
        step = 'replacement'
        bam = f'{self.outdir_dic[sample]["conversion"]}/{sample}.PosTag.bam'
        snp = f'{self.outdir_dic[sample]["conversion"]}/{sample}.snp.csv'
        tsne_file = f'{self.outdir_dic[sample]["analysis"]}/{sample}_tsne_coord.tsv'
        cell = f'{self.outdir_dic[sample]["count"]}/{sample}_{FILTERED_MATRIX_DIR_SUFFIX[0]}/{BARCODE_FILE_NAME}'
        cmd_line = self.get_cmd_line(step, sample)
        bg_para = ''
        if sample in self.col5_dict:
            bg_para = f'--bg {self.col5_dict[sample]} '
        cmd = (
            f'{cmd_line} '
            f'--bam {bam} '
            f'--bg {snp} {bg_para} '
            f'--tsne {tsne_file} '
            f'--cell {cell} '
        )
        self.process_cmd(cmd, step, sample, m=5*int(self.args.thread), x=self.args.thread)



def main():
    multi = Multi_dynaseq(__ASSAY__)
    multi.run()


if __name__ == '__main__':
    main()